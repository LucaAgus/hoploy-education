import math
import json
import logging

from hopwise.utils import PathLanguageModelingTokenType
from hoploy.registry import Wrapper
from hoploy.components import DefaultHopwiseWrapper
from hoploy.core.catalog import get_catalog
from hoploy import logger
from datetime import datetime, timezone
from pathlib import Path

def _item_to_info(record: dict) -> dict:
    """
    Convert a catalog record (e.g. JSON/CSV) into a dictionary
    compatible with the InfoResponse Pydantic model.
    """
    course_name = record.get("name", "").strip()
    
    # Extract and clean the data according to your InfoResponse
    language = record.get("language", "").strip() or None
    
    # Safe handling of the floats for enrolments and price
    try:
        enrolments = float(record.get("enrolments", 0))
    except (ValueError, TypeError):
        enrolments = None
        
    try:
        price = float(record.get("price", 0.0))
    except (ValueError, TypeError):
        price = None

    # If the objectives are a comma-separated string in the dataset, we turn them into a list
    raw_objectives = record.get("objectives", "")
    if isinstance(raw_objectives, str) and raw_objectives:
        try:
            parsed = json.loads(raw_objectives)
            objectives = parsed if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            # fallback for any record that really is comma-separated text,
            # not a JSON array wrapped in a string
            objectives = [obj.strip() for obj in raw_objectives.split(",") if obj.strip()]
    elif isinstance(raw_objectives, list):
        objectives = raw_objectives
    else:
        objectives = None
    return {
        "course": course_name,
        "language": language,
        "enrolments": enrolments,
        "price": price,
        "objectives": objectives
    }


# ---------------------------------------------------------------------------
# Natural language explanations
#
# One sentence per relation of the knowledge graph, keyed by the relation
# name without the reverse suffix. Placeholders: {anchor} is the starting
# course, {rec} the recommended one, {entity} the intermediate node.
# User nodes have no placeholder: their identifier must not appear in the
# response.
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "[UI-Relation]": "Learners who took {anchor} went on to take {rec} as well.",
    "covers_in_concept": "{rec} covers {entity}, one of the topics also addressed by {anchor}.",
    "belong_to_category": "{rec} sits in the same {entity} category as {anchor}.",
    "taught_by_instructor": "{rec} is taught by the same instructor as {anchor}.",
    "has_target_audience": "{rec} is designed for {entity}, the same audience as {anchor}.",
    "taught_in_language": "{rec} is taught in {entity}, like {anchor}.",
    "taught_in_level": "{rec} is pitched at the {entity} level, like {anchor}.",
}

_CHAIN_TEMPLATE = "{rec} is connected to {anchor} through {chain}."
_ANONYMOUS_LEARNER = "other learners"


def _entity_label(entity_name: str) -> str:
    """Turn an entity name into something readable.

    Entities follow the ``Type.Value`` convention and use the underscore
    as a separator, so ``"Category.Social_Science"`` becomes
    ``"Social Science"``.
    """
    value = entity_name.split(".", 1)[1] if "." in entity_name else entity_name
    label = value.replace("_", " ").strip()
    return label.title() if label.islower() else label


def _base_relation(relation_name: str) -> str:
    """Strip the reverse suffix from a relation name."""
    return relation_name[:-2] if relation_name.endswith("_r") else relation_name


def _explain(raw_tokens: list, real_tokens: list) -> tuple:
    """Build the explanation out of the generated path.

    :param raw_tokens: Hopwise tokens of the path, without the leading token.
    :param real_tokens: Corresponding decoded values.
    :returns: Pair ``(text, path_type)``, where the type is the sequence of
        relations traversed.
    :rtype: tuple[str | None, str]
    """
    items, entities, intermediates, relations = [], [], [], []

    for raw, real in zip(raw_tokens, real_tokens):
        if raw.startswith(PathLanguageModelingTokenType.ITEM.token):
            items.append(real)
        elif raw.startswith(PathLanguageModelingTokenType.ENTITY.token):
            label = _entity_label(real)
            entities.append(label)
            intermediates.append(label)
        elif raw.startswith(PathLanguageModelingTokenType.USER.token):
            intermediates.append(_ANONYMOUS_LEARNER)
        elif raw.startswith(PathLanguageModelingTokenType.RELATION.token):
            relations.append(real)

    path_type = "|".join(relations)
    base_relations = {_base_relation(name) for name in relations}

    if len(items) >= 2 and len(intermediates) == 1 and len(base_relations) == 1:
        template = _TEMPLATES.get(base_relations.pop())
        if template and items[0] != items[-1]:
            return template.format(
                anchor=items[0],
                rec=items[-1],
                entity=entities[0] if entities else "",
            ), path_type

    # Shapes without a dedicated sentence, for instance longer paths.
    if len(items) >= 2:
        chain = " and ".join(node for node in intermediates
        )
        return _CHAIN_TEMPLATE.format(
            rec=items[-1],
            anchor=items[0],
            chain=chain or "the knowledge graph",
        ), path_type

    return None, path_type


@Wrapper("education_model")
class EducationWrapper(DefaultHopwiseWrapper):
    """
    Wrapper for university course recommendation.
    Handles the translation of course names into Hopwise IDs and back.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        # Initialise the course catalog
        self._catalog = get_catalog(cfg.dataset)
        logger.info(f"Loaded course catalog: {len(self._catalog.items)} courses")

        # Request log: one JSON line per call, with what the user asked for
        # and what was returned. Switched off when the path is not
        # configured.
        self._stats = None
        stats_path = getattr(cfg, "stats_path", None)
        if stats_path:
            try:
                percorso = Path(stats_path)
                percorso.parent.mkdir(parents=True, exist_ok=True)

                self._stats = logging.getLogger("education.recstat")
                self._stats.setLevel(logging.INFO)
                self._stats.propagate = False
                if not self._stats.handlers:
                    gestore = logging.FileHandler(percorso, encoding="utf-8")
                    gestore.setFormatter(logging.Formatter("%(message)s"))
                    self._stats.addHandler(gestore)
                logger.info(f"Registro delle richieste attivo: {percorso}")
            except OSError as exc:
                self._stats = None
                logger.warning(f"Registro delle richieste non attivabile: {exc}")

    def _names_to_course_ids(self, names: list) -> list:
        """Convert literal course names into catalog/model IDs."""
        result = []
        for name in names:
            course_id = self._catalog.name_index.get(name.lower())

            if course_id is None:
                logger.warning(f"Course name '{name}' not found in catalog, skipping.")
            else:
                result.append(course_id)
        
        return result


    # -- Mandatory pipeline hooks ---------------------------------------------

    def distill(self, request):
        """
        Step 2: Turn the IDs into token strings for the neural network.
        """
        preferences = request.preferences

        # If the user asked for nothing, we return an empty list
        if not preferences:
            logger.error("No preferences provided for zero-shot recommendation.")
            return []
        
        separator = self.dataset.path_token_separator
        bos = self.dataset.tokenizer.bos_token

        # Convert course names to dataset poi_ids, then to hopwise tokens
        raw_inputs = []
        for course_id in self._names_to_course_ids(preferences):
            try:
                token = self.encode(course_id, PathLanguageModelingTokenType.ITEM.token)
                raw_inputs.append(separator.join([bos, token]))
            except KeyError:
                logger.warning(f"course_id '{course_id}' not found in hopwise dataset, skipping.")

        logger.debug(f"Education distill: {len(raw_inputs)} raw inputs")
        
        return raw_inputs

    def handle(self, request):
        """Merge request parameters into runtime configuration.

        :param request: Incoming request.
        :type request: Config
        :returns: Self.
        :rtype: EducationWrapper
        """
        super().handle(request)
        return self


    def expand(self, values, request):
        """Convert raw model output to the education response schema.

        Builds a ``RecommendationResponse`.

        :param values: Tuple of ``(scores, recommendations, explanations)``
            or ``None``.
        :param request: The original recommendation request.
        :type request: Config
        :returns: Response dict or ``None`` when no valid results exist.
        :rtype: dict | None
        """
        if values is None:
            return None

        scores, recommendations, explanations = values

        # Filter inf scores
        valid = [(s, r, e) for s, r, e in zip(scores, recommendations, explanations) if not math.isinf(float(s))]
        if not valid:
            logger.warning(
                "Tutte le %d sequenze restituite hanno punteggio infinito: "
                "nessuna raccomandazione da presentare.",
                len(scores),
            )
            return None
        scores, recommendations, explanations = zip(*valid)

            
        user_id = getattr(request, "user_id", "unknown_user")

        result_items = []


        for score, rec_id, raw_exp in zip(scores, recommendations, explanations):
            # Resolve the record from the dataset id: the name rebuilt from
            # the tokens does not necessarily match the catalog key.
            rec_token = PathLanguageModelingTokenType.ITEM.token + str(rec_id)
            course_id = self.decode(rec_token)
            record = self._catalog.items.get(course_id)
            if record is not None:
                course_name = record.get("name", "").strip()
                metadata = _item_to_info(record)
            else:
                course_name = self.decode(rec_token, real_token=True)
                metadata = self.info(course_name)

            # Build explanation
            raw_tokens = raw_exp[1:]
            real_tokens = [self.decode(t, real_token=True) for t in raw_tokens]
            explanation_text, explanation_type = _explain(raw_tokens, real_tokens)

            # Build the object RecommendationItem
            result_items.append({
                "course": course_name,
                "score": float(score),
                "explanation": explanation_text,
                "explanation_type": explanation_type,
                "metadata": metadata
            })

        if self._stats is not None:
            self._stats.info(json.dumps({
                "istante": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "user_id": user_id,
                "preferenze": list(getattr(request, "preferences", None) or []),
                "gia_viste": len(getattr(request, "previous_recommendations", None) or []),
                "filtri": {
                    campo: list(getattr(request, campo, None) or [])
                    for campo in ("concept_preferences", "category_preferences",
                                  "audience_preferences", "language_preferences",
                                  "level_preferences")
                    if getattr(request, campo, None)
                },
                "solo_contenuto": bool(getattr(request, "disable_collaborative", False)),
                "richieste": int(getattr(request, "recommendation_count", 5) or 5),
                "ottenute": len(result_items),
                "raccomandazioni": [
                    {
                        "corso": r["course"],
                        "punteggio": round(r["score"], 8),
                        "tipo": r["explanation_type"],
                    }
                    for r in result_items
                ],
            }, ensure_ascii=False))
                    
        return {
            "user_id": user_id,
            "recommendations": result_items,
        }

    # -- Additional endpoints (Info / Search) ---------------------------------

    def info(self, request):
        """Look up a course by name and return its metadata.

        :param request: A request with an ``item`` attribute, or a
            plain string course name.
        :returns: ``InfoResponse``-compatible dict, or ``None``.
        :rtype: dict | None
        """
        # 1. Extract the name from the string or from the Request object
        if isinstance(request, str):
            item = request
        else:
            item = request.item
        if not item:
            return None
            
        course_id = self._catalog.name_index.get(item.lower())
        if course_id is None:
            return None

        record = self._catalog.items.get(course_id)
        if record is None:
            return None
            
        return _item_to_info(record)


    def search(self, request):
        """
        Free-text search within the course catalog.
        Implements basic filters when the catalog supports them.
        """
        query = request.query
        limit = int(getattr(request, "limit", 10))

        has_filters = (
            getattr(request, "max_price", None) is not None
            or getattr(request, "languages", None) is not None
        )
        # search() stops as soon as it collects `limit` results, so with the
        # filters active a margin is needed, to be cut after applying them.
        hits = self._catalog.search(query, limit=limit * 10 if has_filters else limit)
                
        results = []
        for hit in hits:
            record = hit["record"]
            # Apply the filters manually when the catalog search does not support them
            if getattr(request, "max_price", None) is not None:
                price = float(record.get("price", 0))
                if price > request.max_price:
                    continue
                    
            if getattr(request, "languages", None) is not None:
                lang = record.get("language", "").lower()
                req_langs = [l.lower() for l in request.languages]
                if lang not in req_langs:
                    continue
                    
            results.append(_item_to_info(record))
            if len(results) >= limit:
                break
        return {"results": results}
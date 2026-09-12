"""Logits processors for the education domain."""

import re

import numpy as np
import torch
from hopwise.utils import PathLanguageModelingTokenType

from hoploy import logger
from hoploy.components import ForcedLogitsProcessor, RestrictedHopwiseLogitsProcessor
from hoploy.core.catalog import get_catalog
from hoploy.core.utils import get_valid_item_ids
from hoploy.registry import LogitsProcessor


_ITEM = PathLanguageModelingTokenType.ITEM.token
_ENTITY = PathLanguageModelingTokenType.ENTITY.token

_UI_RELATION = "[UI-Relation]"

# Score assigned to the padding token when a path under construction is left
# with no admissible choice. It must be finite, so that undefined scores are
# not produced, and far lower than any log-probability: with a neutral value
# the dead-end path would be promoted ahead of those still viable.
_DEAD_PATH_SCORE = -1e9

# Work budget of the reachability check: beyond this number of inspected
# edges the filter is kept without further verification.
_REACHABILITY_BUDGET = 500_000

# Topical fields: they describe an interest and are multi-valued, with
# fifteen concepts per course, eight audiences, two categories. Their values
# are selective, so they are unioned with one another: someone who states a
# category and a concept is describing the same interest in two ways, not
# asking for a course that satisfies both.
_TOPICAL_FIELDS = {
    "concept_preferences": ("concept",),
    "category_preferences": ("category",),
    "audience_preferences": ("audience",),
}

# Attribute fields: every course has exactly one value and each value covers
# many courses. These are conditions proper, so they are intersected with
# the rest.
_ATTRIBUTE_FIELDS = {
    "language_preferences": ("language",),
    "level_preferences": ("level",),
}


def _normalize(value) -> str:
    """Reduce a string to a comparison key of letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _build_entity_index(dataset) -> dict:
    """Index the entity vocabulary by normalized type and value.

    Entities follow the ``Type.Value`` convention, so a value coming from
    the API is resolved without depending on the exact spelling:
    ``"Social Science"`` finds ``Category.Social_Science``.

    :param dataset: The Hopwise dataset.
    :returns: Mapping ``(type, value) -> entity name``.
    :rtype: dict[tuple[str, str], str]
    """
    by_type: dict[tuple[str, str], str] = {}

    for raw_name in dataset.field2id_token[dataset.entity_field]:
        name = str(raw_name)
        prefix, _, value = name.partition(".") if "." in name else ("", "", name)

        value_key = _normalize(value)
        if value_key:
            by_type[(_normalize(prefix), value_key)] = name

    return by_type


def _resolve_entity(value, prefixes, by_type):
    """Resolve an API value into an entity name of the graph.

    :param value: The value received, for instance ``"Beginner"``.
    :param prefixes: Type prefixes accepted for that field.
    :type prefixes: tuple[str, ...]
    :param by_type: Mapping ``(type, value) -> name``.
    :returns: The entity name, or ``None`` when unresolved.
    :rtype: str | None
    """
    value_key = _normalize(value)
    if not value_key:
        return None

    for prefix in prefixes:
        name = by_type.get((_normalize(prefix), value_key))
        if name is not None:
            return name

    logger.warning(f"Filtro '{value}' non trovato nel knowledge graph, ignorato.")
    return None


@LogitsProcessor("education_logits_processor")
class EducationLogitsProcessor(ForcedLogitsProcessor):
    """Graph-constrained processor for the education domain.

    Excludes the courses the user already knows, applies the relation
    patterns declared in the configuration and, on request, removes user
    nodes from the generated paths.

    :param dataset: The Hopwise dataset.
    :param cfg: Configuration section of the processor.
    :type cfg: ~hoploy.core.config.Config
    """

    def __init__(self, dataset, cfg, **kwargs):
        super().__init__(dataset, cfg, **kwargs)

        self._catalog = get_catalog(getattr(cfg, "dataset", "dataset"))
        self._valid_ids = get_valid_item_ids(dataset)

        # Instances are built once and reused for every request, so any
        # per-request state needs its own restore.
        self._configured_force_paths = [
            list(path) for path in (getattr(cfg, "force_paths", None) or [])
        ]
        self._default_remove_user_tokens = bool(
            getattr(cfg, "remove_user_tokens_from_sequences", False)
        )


    # -- name translation ------------------------------------------------------

    def _names_to_item_tokens(self, names) -> list:
        """Convert course names into Hopwise item tokens.

        :param names: Human-readable course names.
        :type names: list[str]
        :returns: Hopwise tokens, for instance ``["I42", "I7"]``.
        :rtype: list[str]
        """
        tokens = []
        for name in names:
            course_id = self._catalog.name_index.get(str(name).lower())
            if course_id is None:
                logger.warning(f"Corso '{name}' non trovato nel catalogo, ignorato.")
                continue
            if course_id not in self._valid_ids:
                logger.warning(f"Corso '{course_id}' assente dal vocabolario del modello, ignorato.")
                continue
            try:
                tokens.append(self.encode(course_id, _ITEM))
            except KeyError:
                logger.warning(f"Corso '{course_id}' non codificabile, ignorato.")
        return tokens

    # -- direction-agnostic patterns -------------------------------------------

    def _relation_variants(self, relation_name) -> set:
        """Return the tokens of the relation in both directions.

        For the same link the graph holds both the direct form and the
        reverse one with the ``_r`` suffix, and the model may use either. A
        pattern written with either name therefore accepts both, and the
        direction does not have to be guessed.

        :param relation_name: Relation name, with or without the suffix.
            ``"[UI-Relation]"`` denotes the user-item relation.
        :type relation_name: str
        :returns: Tokens admitted at that position of the pattern.
        :rtype: set[int]
        """
        name = self.dataset.ui_relation if relation_name == _UI_RELATION else relation_name
        base = name[:-2] if name.endswith("_r") else name

        token_ids = set()
        for variant in (base, f"{base}_r"):
            try:
                token_ids.add(self.get_relation_id(variant))
            except (ValueError, KeyError):
                continue
        return token_ids

    def set_force_paths(self, paths):
        """Resolve the patterns accepting both directions of each relation.

        Every position of the pattern becomes a set of tokens instead of a
        single token.

        :param paths: Patterns as lists of relation names.
        :type paths: list[list[str]] | None
        """
        if not paths:
            self._force_paths = []
            return

        resolved = []
        for path in paths:
            steps = []
            for relation_name in path:
                variants = self._relation_variants(relation_name)
                if not variants:
                    logger.warning(
                        f"Relazione '{relation_name}' assente dal dataset, pattern ignorato."
                    )
                    steps = None
                    break
                steps.append(frozenset(variants))
            if steps is not None:
                resolved.append(steps)
        self._force_paths = resolved

    def _get_allowed_relations(self, candidates, relation_sequence):
        """Select the candidates admitted by the patterns at the current depth.

        :param candidates: Relation tokens offered by the graph.
        :type candidates: set[int]
        :param relation_sequence: Relations already generated in the path.
        :type relation_sequence: list[int]
        :returns: Admitted tokens, possibly empty.
        :rtype: set[int]
        """
        depth = len(relation_sequence)
        allowed = set()
        for path in self._force_paths:
            if depth >= len(path):
                continue
            if all(token in step for token, step in zip(relation_sequence, path)):
                allowed |= path[depth] & candidates
        return allowed

    # -- request hook ----------------------------------------------------------

    def handle(self, request):
        """Configure the processor for the incoming request.

        The courses already known, both the declared preferences and the
        previous recommendations, are masked: paths start from the
        preferences, so without this mask the starting course could come
        back as a recommendation. With ``disable_collaborative`` active,
        user nodes and the user-item relation leave the candidate sets and
        the collaborative pattern leaves the forced paths.

        :param request: The API request.
        :type request: Config
        :returns: ``self``.
        :rtype: EducationLogitsProcessor
        """
        super().handle(request)
        known_names = list(getattr(request, "previous_recommendations", None) or [])
        known_names.extend(getattr(request, "preferences", None) or [])
        item_tokens = self._names_to_item_tokens(known_names)
        self.set_previous_recommendations(item_tokens or None)

        disable_collaborative = bool(getattr(request, "disable_collaborative", False))
        self.remove_user_tokens_from_sequences = (
            True if disable_collaborative else self._default_remove_user_tokens
        )

        force_paths = self._configured_force_paths
        if disable_collaborative:
            force_paths = [path for path in force_paths if _UI_RELATION not in path]
        self.set_force_paths(force_paths)

        logger.debug(
            "Education logits processor: %d corsi mascherati, %d pattern, "
            "percorsi collaborativi %s",
            len(item_tokens),
            len(force_paths),
            "disattivati" if disable_collaborative else "attivi",
        )
        return self

@LogitsProcessor("education_restricted_logits_processor")
class EducationRestrictedLogitsProcessor(RestrictedHopwiseLogitsProcessor):
    """Positive filter on the admissible courses.

    Translates the preference fields of the request into graph entities and
    bans the tokens of the courses not connected to those entities. Entity
    tokens stay free, so the intermediate nodes of the paths are untouched
    and explanations by concept, category or instructor remain possible: the
    filter constrains the destination, not the route.

    Values of the same field are always unioned. Across different fields the
    rule depends on the type: topical fields are unioned with one another,
    attribute fields are intersected with the rest. The reason lies in the
    shape of the graph, where a concept covers about thirty recommendable
    courses on average while a language covers hundreds or thousands:
    intersecting two topical fields almost always yields the empty set.

    :param dataset: The Hopwise dataset.
    :param cfg: Configuration section of the processor.
    :type cfg: ~hoploy.core.config.Config
    """

    def __init__(self, dataset, cfg, **kwargs):
        super().__init__(dataset, cfg, **kwargs)

        self._catalog = get_catalog(getattr(cfg, "dataset", "dataset"))
        self._entity_by_type = _build_entity_index(dataset)

        # Reachability threshold: when fewer allowed courses than this
        # number can be reached from the declared preferences, the filters
        # are dropped. Imperfect recommendations beat no answer at all.
        self._min_reachable = int(getattr(cfg, "min_reachable_courses", 5))

        # Level that encompasses all the others, if the dataset has one. It
        # is added to the requested levels, so that asking for the beginner
        # level also returns the courses suitable for anyone.
        self._generic_level = "All"

        self._item_token_ids = np.array(
            [
                token_id
                for token, token_id in self.tokenizer.get_vocab().items()
                if token.startswith(_ITEM)
            ],
            dtype=np.int64,
        )
        self._item_tokens = set(self._item_token_ids.tolist())

        # Courses outside the model vocabulary still exist in the graph as
        # "Course.N" entities. They are not recommendable, because reading
        # the sequences requires an item token as the last element, so a
        # path crossing them is lost: banning them prevents them from
        # becoming the only way out when the filter is narrow.
        self._course_entity_tokens = self._collect_course_entities(dataset)

        # The mask is computed once per request and reused at every step of
        # the generation.
        self._mask = None
        self._mask_tensor = None

        #logger.info(
         #   "Corsi presenti nel grafo ma non raccomandabili: "
          #  f"{len(self._course_entity_tokens)}"
        #)

    # -- startup survey --------------------------------------------------------

    def _collect_course_entities(self, dataset):
        """Tokens of the entities standing for non-recommendable courses.

        :param dataset: The Hopwise dataset.
        :returns: Array of the tokens to ban.
        :rtype: numpy.ndarray
        """
        tokens = []
        for entity_id, name in enumerate(dataset.field2id_token[dataset.entity_field]):
            if not str(name).startswith("Course."):
                continue
            token_id = self.tokenizer.convert_tokens_to_ids(_ENTITY + str(entity_id))
            if token_id != self.tokenizer.unk_token_id:
                tokens.append(token_id)
        return np.array(sorted(tokens), dtype=np.int64)

    # -- from names to admitted courses ----------------------------------------

    def _field_tokens(self, request, field, prefixes) -> list:
        """Translate the values of a request field into entity tokens.

        :param request: The API request.
        :param field: Field name, for instance ``"concept_preferences"``.
        :type field: str
        :param prefixes: Type prefixes accepted for that field.
        :type prefixes: tuple[str, ...]
        :returns: Tokens of the resolved entities.
        :rtype: list[int]
        """
        values = list(getattr(request, field, None) or [])
        if not values:
            return []

        # The generic level holds alongside any requested level.
        if field == "level_preferences" and self._generic_level:
            values.append(self._generic_level)

        token_ids = []
        for value in values:
            name = _resolve_entity(value, prefixes, self._entity_by_type)
            if name is None:
                continue
            try:
                hopwise_id = self.encode(name, _ENTITY)
            except KeyError:
                logger.warning(f"Entita' '{name}' non codificabile, ignorata.")
                continue
            token_ids.extend(self._hopwise_ids_to_token_ids([hopwise_id]))
        return token_ids

    def _reachable_items(self, entity_token_ids) -> set:
        """Recommendable courses connected to at least one of the entities.

        :param entity_token_ids: Tokens of the entities.
        :type entity_token_ids: list[int]
        :returns: Tokens of the courses reachable in one step.
        :rtype: set[int]
        """
        reachable = set()
        for token_id in entity_token_ids:
            reachable |= self._extract_connected_entities(token_id) & self._item_tokens
        return reachable

    def _allowed_courses(self, topical, attributes) -> set:
        """Combine the request fields into the set of admitted courses.

        :param topical: Entity tokens of the topical fields, already unioned.
        :type topical: list[int]
        :param attributes: One group of tokens per attribute field.
        :type attributes: list[list[int]]
        :returns: Tokens of the admitted courses, possibly empty.
        :rtype: set[int]
        """
        allowed = self._reachable_items(topical) if topical else None

        for group in attributes:
            reachable = self._reachable_items(group)
            allowed = reachable if allowed is None else (allowed & reachable)
            if not allowed:
                return set()

        return allowed or set()

    # -- reachability from the preferences -------------------------------------

    def _anchor_tokens(self, request) -> list:
        """Tokens of the courses the paths start from.

        :param request: The API request.
        :returns: Tokens of the courses declared as preferences.
        :rtype: list[int]
        """
        tokens = []
        for name in (getattr(request, "preferences", None) or []):
            course_id = self._catalog.name_index.get(str(name).lower())
            if course_id is None:
                continue
            try:
                hopwise_id = self.encode(course_id, _ITEM)
            except KeyError:
                continue
            tokens.extend(self._hopwise_ids_to_token_ids([hopwise_id]))
        return tokens

    def _count_reachable(self, anchors, allowed, required, skip_users) -> tuple:
        """Count the admitted courses reachable in two steps from the preferences.

        The count stops as soon as it reaches the threshold, so the normal
        case is cheap; the worst case, the one where nothing is reached, is
        bounded by a work budget beyond which the filter is kept without
        further verification.

        :param anchors: Tokens of the starting courses.
        :type anchors: list[int]
        :param allowed: Tokens of the courses admitted by the filters.
        :type allowed: set[int]
        :param required: How many courses suffice for the filter to be
            applicable.
        :type required: int
        :param skip_users: Whether to ignore user nodes, as happens when
            collaborative paths are disabled.
        :type skip_users: bool
        :returns: Pair ``(how_many_found, budget_reached)``.
        :rtype: tuple[int, bool]
        """
        found = set()
        work = 0

        for anchor in anchors:
            for tails in self.tokenized_ckg.get(anchor, {}).values():
                for middle in tails:
                    if skip_users and middle in self.tokenized_uids:
                        continue
                    for second_tails in self.tokenized_ckg.get(middle, {}).values():
                        work += len(second_tails)
                        for tail in second_tails:
                            if tail in allowed:
                                found.add(tail)
                                if len(found) >= required:
                                    return len(found), False
                        if work >= _REACHABILITY_BUDGET:
                            return len(found), True

        return len(found), False

    # -- mask ------------------------------------------------------------------

    def _build_mask(self, allowed):
        """Build the mask of the tokens to ban.

        :param allowed: Tokens of the admitted courses.
        :type allowed: set[int]
        :returns: Boolean array as long as the vocabulary.
        :rtype: numpy.ndarray
        """
        mask = np.zeros(len(self.tokenizer), dtype=bool)
        mask[self._item_token_ids] = True
        mask[list(allowed)] = False
        if len(self._course_entity_tokens):
            mask[self._course_entity_tokens] = True
        return mask

    def __call__(self, input_ids, scores):
        """Apply the mask computed in :meth:`handle`.

        When no token is left admissible for a path under construction, that
        is when the courses valid on the graph are all excluded by the
        filter, the padding token is released with a very low score. Without
        this safeguard the row would stay entirely at minus infinity, the
        scores would become undefined and the choice would fall on arbitrary
        tokens of the vocabulary.

        :param input_ids: Tokens of the paths under construction.
        :param scores: Scores to modify.
        :returns: The modified scores.
        """
        if self._mask is None:
            return scores

        if self._mask_tensor is None or self._mask_tensor.device != scores.device:
            self._mask_tensor = torch.from_numpy(self._mask).to(scores.device)

        scores[:, self._mask_tensor] = -torch.inf

        exhausted = torch.logical_not(torch.isfinite(scores)).all(dim=-1)
        if exhausted.any():
            scores[exhausted, self.tokenizer.pad_token_id] = _DEAD_PATH_SCORE
            logger.debug(
                "Filtro education: %d percorsi senza uscite, terminati.",
                int(exhausted.sum()),
            )

        return scores

    # -- request hook ----------------------------------------------------------

    def clear_restrictions(self):
        """Remove the active filter."""
        super().clear_restrictions()
        self._mask = None
        self._mask_tensor = None

    def handle(self, request):
        """Translate the preference fields of the request into a mask.

        The filter is dropped, with a note in the log, in two cases: when no
        course satisfies the requested fields, and when not enough of them
        are reachable in two steps from the declared preferences. In both
        cases the request receives unfiltered results instead of an empty
        answer.

        :param request: The API request.
        :type request: Config
        :returns: ``self``.
        :rtype: EducationRestrictedLogitsProcessor
        """
        self.clear_restrictions()

        topical = []
        for field, prefixes in _TOPICAL_FIELDS.items():
            topical.extend(self._field_tokens(request, field, prefixes))

        attributes = []
        for field, prefixes in _ATTRIBUTE_FIELDS.items():
            tokens = self._field_tokens(request, field, prefixes)
            if tokens:
                attributes.append(tokens)

        if not topical and not attributes:
            return self

        allowed = self._allowed_courses(topical, attributes)
        if not allowed:
            logger.warning(
                "I filtri richiesti non selezionano alcun corso: annullati, "
                "la richiesta riceve risultati non filtrati."
            )
            return self

        anchors = self._anchor_tokens(request)
        if anchors and self._min_reachable > 0:
            # No more courses can be demanded than the filter admits: if it
            # admits a single one and that one is reachable, the filter has
            # worked and must be applied.
            required = min(self._min_reachable, len(allowed))
            skip_users = bool(getattr(request, "disable_collaborative", False))
            found, budget_reached = self._count_reachable(
                anchors, allowed, required, skip_users
            )
            if not budget_reached and found < required:
                logger.warning(
                    "Dalle preferenze dichiarate si raggiungono %d corsi dei %d "
                    "ammessi dai filtri, ne servivano %d: filtri annullati per "
                    "questa richiesta.",
                    found,
                    len(allowed),
                    required,
                )
                return self

        self._mask = self._build_mask(allowed)
        logger.info(f"Filtro education: {len(allowed)} corsi ammessi")
        return self
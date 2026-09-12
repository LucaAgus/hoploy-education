"""Sequence score post-processor for the education domain."""

import torch
from hopwise.utils import PathLanguageModelingTokenType

from hoploy import logger
from hoploy.components import ForcedSequenceScorePostProcessor
from hoploy.registry import SequenceProcessor


_UI_RELATION = "[UI-Relation]"


@SequenceProcessor("education_sequence_processor")
class EducationSequenceProcessor(ForcedSequenceScorePostProcessor):
    """Filter and reorder the generated sequences.

    Keeps only the paths whose relation sequence matches one of the
    configured patterns and, on request, promotes the best one for each
    pattern, so that a single answer carries explanations of different
    shapes.

    When no generated path matches the patterns the filter is dropped,
    consistently with the choice adopted for the graph filters as well: a
    weak explanation is worth more than an empty answer.

    :param dataset: The Hopwise dataset.
    :param cfg: Configuration section of the processor.
    :type cfg: ~hoploy.core.config.Config
    """

    def __init__(self, dataset, cfg, **kwargs):
        super().__init__(dataset, cfg, **kwargs)

        self._configured_force_paths = [
            list(path) for path in (getattr(cfg, "force_paths", None) or [])
        ]

        # Promotion rewrites the score of the promoted sequences, which
        # therefore stop being comparable with the others. It must be
        # disabled whenever the scores are used for a measurement.
        self._diversify = bool(getattr(cfg, "diversify_by_path_type", False))

        # Per-sequence score produced by beam search, kept by get_sequences
        # and used by calculate_sequence_scores.
        self._sequences_scores = None

    # -- direction-agnostic patterns -------------------------------------------

    def _relation_variant_tokens(self, relation_name) -> set:
        """Return the tokens of the relation in both directions.

        :param relation_name: Relation name, with or without the ``_r``
            suffix. ``"[UI-Relation]"`` denotes the user-item relation.
        :type relation_name: str
        :returns: Admitted relation tokens, for instance ``{"R5", "R11"}``.
        :rtype: set[str]
        """
        name = self._dataset.ui_relation if relation_name == _UI_RELATION else relation_name
        base = name[:-2] if name.endswith("_r") else name
        lookup = self._dataset.field2token_id[self._dataset.relation_field]

        tokens = set()
        for variant in (base, f"{base}_r"):
            relation_id = lookup.get(variant)
            if relation_id is not None:
                tokens.add(PathLanguageModelingTokenType.RELATION.token + str(relation_id))
        return tokens

    def _set_force_paths(self, paths):
        """Resolve the patterns accepting both directions of each relation.

        :param paths: Patterns as lists of relation names.
        :type paths: list[list[str]]
        """
        resolved = []
        for path in paths or []:
            steps = []
            for relation_name in path:
                variants = self._relation_variant_tokens(relation_name)
                if not variants:
                    logger.warning(
                        f"Relazione '{relation_name}' assente dal dataset, pattern ignorato."
                    )
                    steps = None
                    break
                steps.append(frozenset(variants))
            if steps is not None:
                resolved.append(steps)
        self._tokenized_sequence_paths = resolved

    def _get_sequence_type(self, sequence):
        """Return the index of the pattern the sequence matches.

        :param sequence: Tokens of a generated sequence.
        :returns: Index of the pattern, or ``-1``.
        :rtype: int
        """
        prefix = PathLanguageModelingTokenType.RELATION.token
        decoded = self.tokenizer.decode(sequence).split(" ")
        relation_tokens = [token for token in decoded if token.startswith(prefix)]

        for index, path in enumerate(self._tokenized_sequence_paths):
            if len(relation_tokens) != len(path):
                continue
            if all(token in step for token, step in zip(relation_tokens, path)):
                return index
        return -1

    def force_paths_mask(self, sequences):
        """Mark the sequences that match one of the patterns.

        If no sequence matches, the filter is dropped for that request and a
        note is left in the log: the user receives recommendations carrying a
        weak explanation, by language or level, instead of an empty answer.
        The case shows up when the starting courses have no admitted link in
        the graph.

        :param sequences: Tokens of the generated sequences.
        :returns: Boolean tensor as long as the sequences.
        :rtype: torch.BoolTensor
        """
        matches = [self._get_sequence_type(sequence) != -1 for sequence in sequences]

        if not any(matches):
            logger.warning(
                "Nessuna sequenza generata corrisponde ai pattern configurati: "
                "filtro sui pattern annullato per questa richiesta."
            )
            return torch.ones(len(matches), dtype=torch.bool, device=sequences.device)

        return torch.tensor(matches, dtype=torch.bool, device=sequences.device)

    def surface_top_sequences_by_type(self, sequences, sequences_scores):
        """Promote the best path of each pattern, when enabled.

        :param sequences: Tokens of the generated sequences.
        :param sequences_scores: Corresponding scores.
        :returns: The scores, modified or unchanged.
        :rtype: torch.Tensor
        """
        if not self._diversify:
            return sequences_scores
        return super().surface_top_sequences_by_type(sequences, sequences_scores)

    # -- sequence score --------------------------------------------------------

    def get_sequences(self, generation_outputs, user_num=1, max_new_tokens=24, previous_recommendations=None):
        """Keep the score computed by beam search, then delegate.

        :param generation_outputs: Output of ``model.generate``.
        :returns: Pair ``(scores, sequences)``.
        :rtype: tuple
        """
        self._sequences_scores = generation_outputs.get("sequences_scores")
        return super().get_sequences(
            generation_outputs,
            user_num=user_num,
            max_new_tokens=max_new_tokens,
            previous_recommendations=previous_recommendations,
        )

    def calculate_sequence_scores(self, normalized_tuple, sequences, max_new_tokens=24):
        """Use the score beam search has already computed.

        The distributions returned by the generation have one row per beam,
        while the sequences are those selected at the end: there is no
        positional correspondence between the two, so recomputing the score
        by picking the row of equal index pairs every sequence with a beam
        that is not its own. Beam search instead keeps the correct score of
        each returned sequence, normalized by length; its exponential brings
        it back to a value between zero and one, readable as an average
        per-token probability.

        Should the score be unavailable, the computation falls back to the
        library one.

        :param normalized_tuple: Distributions per generation step.
        :param sequences: Generated sequences.
        :param max_new_tokens: Tokens generated per sequence.
        :returns: Score of every sequence.
        :rtype: torch.Tensor
        """
        scores = self._sequences_scores
        if scores is None or scores.shape[0] != sequences.shape[0]:
            logger.warning(
                "Punteggio della ricerca a fasci non disponibile, si usa il "
                "calcolo della libreria."
            )
            return super().calculate_sequence_scores(normalized_tuple, sequences, max_new_tokens)

        return scores.exp()

    def handle(self, request):
        """Align the accepted patterns with the incoming request.

        The list stays consistent with the one applied during decoding, so
        that a request excluding collaborative paths does not find them
        among the accepted patterns.

        :param request: The API request.
        :type request: Config
        :returns: ``self``.
        :rtype: EducationSequenceProcessor
        """
        force_paths = self._configured_force_paths
        if bool(getattr(request, "disable_collaborative", False)):
            force_paths = [path for path in force_paths if _UI_RELATION not in path]

        self._set_force_paths(force_paths)
        logger.debug(
            "Education sequence processor: %d pattern accettati, diversificazione %s",
            len(force_paths),
            "attiva" if self._diversify else "disattiva",
        )
        return self
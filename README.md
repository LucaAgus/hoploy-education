

# 🎓 Education Plugin for Hoploy

> A [Hoploy](https://github.com/tail-unica/hoploy) plugin for explainable course recommendation over educational knowledge graphs, with support for cold-start learners through preference elicitation.

This plugin extends [Hoploy](https://github.com/tail-unica/hoploy) — the inference and explanation layer for path-reasoning models built on [Hopwise](https://github.com/tail-unica/hopwise) — to the educational domain. It provides the domain-specific components needed to serve PEARLM and KGGLM models trained on the [COCO](https://dl.acm.org/doi/10.1007/978-3-319-97571-9_116) educational knowledge graph, and exposes a REST API through which a learner with no interaction history can receive course recommendations together with the reasoning paths that produced them.

This plugin was developed as part of the Bachelor's thesis *"Explainable Reasoning over Knowledge Graphs for Cold-Start Educational Recommendation"*, Università degli Studi di Cagliari, A.Y. 2025/2026.

---

## Overview

Path-reasoning models generate recommendations by traversing a knowledge graph from a starting token. For a learner with an interaction history, that starting point is their learned representation in the graph. For a learner arriving for the first time — the cold-start condition — no such representation exists. This plugin addresses that gap by accepting explicitly elicited course preferences as the starting point for path generation, substituting the absent learner token with item-level context at inference time.

Each recommendation is returned together with its reasoning path rendered as a natural-language sentence, so that the connection between the selected courses and the recommended one is visible to the learner.

---

## Plugin Components

The plugin is structured according to the three extension points defined by Hoploy.

### Wrapper — `EducationWrapper`

The central component. It resolves course names to knowledge graph tokens, feeds them into the path generation procedure as item-anchored prefixes, and converts the generated paths into responses. Each path is decoded into a natural-language explanation by mapping the traversed relation to a template (e.g. *"X covers Psychology, one of the topics also addressed by Y"*). The wrapper also exposes two auxiliary endpoints: `info` for course metadata lookup and `search` for catalogue search with optional filters.

### Logits Processors

Two logits processors are active, applied in declaration order.

**`EducationLogitsProcessor`** constrains which relation patterns may appear in a generated path. Only the following two-hop patterns are accepted:

| Pattern | Meaning |
|---------|---------|
| `[UI-Relation] → [UI-Relation]` | Collaborative path through another learner |
| `covers_in_concept → covers_in_concept` | Shared concept |
| `belong_to_category → belong_to_category` | Shared category |
| `taught_by_instructor → taught_by_instructor` | Same instructor |
| `has_target_audience → has_target_audience` | Same target audience |

`taught_in_language` and `taught_in_level` are deliberately excluded from the accepted patterns: both attributes connect a large share of the catalogue and would produce explanations with little informative content. They act as hard filters on the recommended courses instead (see below). The processor also supports the `disable_collaborative` request flag, which removes user-to-user paths so that every explanation rests on a shared course attribute.

**`EducationRestrictedLogitsProcessor`** constrains which courses may appear as the final node of a generated path, based on the topical and attributive preferences declared in the request. Topical fields (`concept_preferences`, `category_preferences`, `audience_preferences`) are combined by union: a course is allowed if it is reachable from any value across these three fields. Attributive fields (`language_preferences`, `level_preferences`) are then combined by intersection: a course must additionally satisfy every attributive constraint stated. If fewer than `min_reachable_courses` (default: 5) allowed courses are reachable from the selected courses, the restriction is dropped for that request and the event is logged.

### Sequence Processor — `EducationSequenceProcessor`

Post-processes complete generated paths before they are returned to the wrapper. Re-applies the same relation-pattern constraint used during decoding, discarding any path whose relations do not match the accepted patterns. If no generated path satisfies the constraint, the check is dropped for that request rather than returning an empty response. An optional `diversify_by_path_type` setting promotes the single best path of each accepted pattern, so that a response may present more than one kind of explanation; this setting rewrites the scores of promoted paths and must be disabled whenever scores are used for measurement.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/info` | Returns metadata for a course by name |
| `POST` | `/search` | Text search over the course catalogue with optional filters |
| `POST` | `/recommend` | Returns ranked course recommendations with reasoning paths |

### `GET /info`

```json
{ "item": "Learn Social Psychology" }
```

Returns course name, language, enrolment count, price, and learning objectives.

### `POST /search`

```json
{
  "query": "machine learning",
  "limit": 5,
  "max_price": 20.0,
  "languages": ["english"]
}
```

### `POST /recommend`

The `preferences` field lists the courses a learner has explicitly selected; they serve as the anchors from which path generation begins. All other fields are optional.

```json
{
  "user_id": "12345",
  "preferences": ["Learn Social Psychology", "An Introduction to Psychology"],
  "recommendation_count": 5,
  "diversity_factor": 0.7,
  "previous_recommendations": ["Introduction to Political Psychology"],
  "disable_collaborative": false,
  "concept_preferences": ["Psychology"],
  "category_preferences": ["Academics"],
  "language_preferences": ["English"],
  "level_preferences": ["Beginner"],
  "audience_preferences": ["Teaching Professionals"],
  "instructor_preferences": ["Andrew Ng"]
}
```

A sample response item:

```json
{
  "course": "Deep Learning Specialization",
  "score": 0.923,
  "explanation": "Deep Learning Specialization covers Neural Networks, one of the topics also addressed by Learn Social Psychology.",
  "metadata": {
    "course": "Deep Learning Specialization",
    "language": "english",
    "enrolments": 45200,
    "price": 49.99,
    "objectives": ["Understand deep neural networks", "..."]
  }
}
```

---

## Knowledge Graph

The plugin was developed and evaluated on the **COCO** dataset (Semantic-Enriched Collection of Online Courses, Dessì et al. 2018), structured as a collaborative knowledge graph with the following relation types:

| Relation | Description |
|----------|-------------|
| `belong_to_category` | Course → Category |
| `covers_in_concept` | Course → Concept |
| `taught_in_level` | Course → Difficulty level |
| `taught_in_language` | Course → Language |
| `taught_by_instructor` | Course → Instructor |
| `has_target_audience` | Course → Target audience |

Inverse relations and the learner–course interaction relation are added automatically by the framework at load time, bringing the total to 14 relation types.

---

## Installation

This plugin requires a working installation of [Hoploy](https://github.com/tail-unica/hoploy). Clone Hoploy first and follow its setup instructions, then clone this repository:

```bash
git clone https://github.com/tail-unica/hoploy
git clone https://github.com/LucaAgus/hoploy-education
```

Refer to the [Hoploy documentation](https://github.com/tail-unica/hoploy) for deployment instructions. Once the service is running, the API documentation is available at `http://localhost:8100/docs` and the health check endpoint at `GET /health`.

---

## Configuration

The plugin is configured through `plugins/education/config.yaml`. The key sections are:

```yaml
plugin:
  name: education
  wrapper: [education_model]
  logits_processors: [education_logits_processor, education_restricted_logits_processor]
  sequence_processor: [education_sequence_processor]
  schema:
    module: schemas
    get:
      info: education_model.info
    post:
      recommend: run
      search: education_model.search

wrapper:
  pearlm:                        # switch to kgglm: to use the other backbone
    name: education_model
    device: "cuda:0"             # or "cpu"
    recommendation_count: 5
    diversity_factor: 0.5
    load_col_item: ["course_id", "name"]
    stats_path: "/app/stats/recstat.jsonl"

logits_processors:
  education_logits_processor:
    name: education_logits_processor
    remove_user_tokens_from_sequences: false
    force_paths:
      - ['[UI-Relation]', '[UI-Relation]']
      - ['covers_in_concept', 'covers_in_concept']
      - ['belong_to_category', 'belong_to_category']
      - ['taught_by_instructor', 'taught_by_instructor']
      - ['has_target_audience', 'has_target_audience']

  education_restricted_logits_processor:
    name: education_restricted_logits_processor
    min_reachable_courses: 5

sequence_processor:
  education_sequence_processor:
    name: education_sequence_processor
    diversify_by_path_type: false   # enable for demos only, not for measurement
    force_paths:
      - ['[UI-Relation]', '[UI-Relation]']
      - ['covers_in_concept', 'covers_in_concept']
      - ['belong_to_category', 'belong_to_category']
      - ['taught_by_instructor', 'taught_by_instructor']
      - ['has_target_audience', 'has_target_audience']
```

To switch between PEARLM and KGGLM, change the top-level key under `wrapper` from `pearlm` to `kgglm`; no other modification is required. Refer to the [Hopwise documentation](https://github.com/tail-unica/hopwise) for the full list of model-level parameters.

---

## Models

The plugin is compatible with the path-reasoning models implemented in [Hopwise](https://github.com/tail-unica/hopwise): **PEARLM** and **KGGLM**. Both models are decoder-only Transformers that generate reasoning paths as token sequences under Knowledge Graph Constrained Decoding, which ensures that every step in the generated path follows an edge present in the graph. They differ in their training procedure: PEARLM is trained directly on learner-centric paths, while KGGLM adds a pretraining phase on generic paths sampled without constraints on their endpoints.

Pre-trained checkpoints on COCO are not distributed in this repository. Instructions for training both models are available in the [Hopwise repository](https://github.com/tail-unica/hopwise).

---

## Repository Structure

```
hoploy-education/
├── frontend/              # Web interface for preference elicitation and recommendation display
├── hoploy/                # Hoploy framework (submodule / copy)
├── plugins/
│   └── education/
│       ├── config.yaml            # Plugin configuration
│       ├── schemas.py             # Request and response models (info, search, recommend)
│       ├── wrapper.py             # EducationWrapper: input distillation and output rendering
│       ├── logits_processors.py   # EducationLogitsProcessor, EducationRestrictedLogitsProcessor
│       └── sequence_processor.py  # EducationSequenceProcessor: path post-processing
├── train/                 # Training scripts and configuration for PEARLM and KGGLM on COCO
├── Dockerfile
├── compose.yaml
├── pyproject.toml
└── uv.lock
```

---

## References

- L. Boratto, G. Fenu, M. Marras, G. Medda, A. Sechi. *hoploy: a plugin-based inference layer for path-based explainable recommendation over knowledge graphs.* RecSys, ACM, 2026.
- G. Balloccu, L. Boratto, C. Cancedda, G. Fenu, M. Marras. *Faithful path language modeling for explainable recommendation over knowledge graph.* 2024.
- G. Balloccu, L. Boratto, G. Fenu, M. Marras, A. Soccol. *KGGLM: A generative language model for generalizable knowledge graph representation learning in recommendation.* RecSys, ACM, 2024.
- D. Dessì, G. Fenu, M. Marras, D. Reforgiato Recupero. *COCO: Semantic-enriched collection of online courses at scale with experimental use cases.* WorldCIST, Springer, 2018.decoding paradigm used in path-reasoning models. Multiple processors can be composed in declaration order. Processors operate on KG-aware candidate representations exposed by the framework, so plugin authors express decoding behaviour in terms of entities and relations without manipulating tokenizer internals directly.


## Tutorial
You can find a tutorial that walks through building and deploying a Hoploy plugin from scratch here: [Hoploy](https://github.com/tail-unica/hoploy).


https://github.com/user-attachments/assets/d6a78d46-0a5c-4c25-9b51-3a80968fafd9


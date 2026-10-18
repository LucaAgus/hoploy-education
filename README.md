

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

The framework provides default processors for common behaviours: graph-valid traversal constraints, masking of previously recommended items, entity restrictions, and relation-pattern forcing. Plugins can reuse these defaults or specialize them.

#### Sequence Processor

The sequence processor operates after candidate paths have been produced and before they are returned to the wrapper for expansion. Unlike logits processors, it receives complete generated paths and can therefore apply operations such as duplicate removal, invalid sequence filtering, score-based sorting, or domain-specific re-ranking. This component is optional.

### Request Lifecycle

For each incoming recommendation request, Hoploy executes a fixed four-step sequence:

1. **Input distillation** — the wrapper maps the request payload to model-compatible inputs. Selected items are resolved through the catalog and converted into the representation expected by the Hopwise model.
2. **Request-specific configuration** — the wrapper and optional processors derive inference parameters from the request (number of recommendations, entity restrictions, etc.).
3. **Path generation** — the framework executes the recommendation procedure, running the underlying Hopwise model to produce candidate reasoning paths. Plugin-defined logits and sequence processors influence decoding through their configured hooks.
4. **Output expansion** — the wrapper converts the generated paths back into KG triples, enriches them with catalog metadata, and renders the application response including recommendations and human-readable explanations.

Plugins can customize how requests are interpreted, how decoding is constrained, how generated paths are post-processed, and how explanations are rendered. They do not re-implement the generic inference workflow, the model prediction procedure, or the token selection loop.


## Tutorial

This tutorial walks through building and deploying a Hoploy plugin from scratch. The following assumes a Docker-based deployment. Before proceeding, ensure Docker is installed on your machine.

### Step 0: Clone and build

```bash
git clone https://github.com/tail-unica/hoploy
cd hoploy
docker compose build
```

The build step resolves all dependencies from the lockfile, ensuring a reproducible environment across machines.

### Step 1: Create the plugin directory

A Hoploy plugin is a Python package. Create a directory with the following structure:

```
your-plugin/
├── __init__.py
├── config.yaml
├── your_schema.py
└── your_processors.py
```

No framework internals need to be modified — all domain-specific logic lives entirely inside this directory.

### Step 2: Define your schemas

Schemas define the shape of your API: what each endpoint accepts and what it returns. Hoploy uses them to automatically validate requests and generate the API surface at startup. Define them as Pydantic models decorated with `@Request` and `@Response`.

```python
# your-plugin/your_schema.py
from pydantic import BaseModel
from hoploy import Request, Response

@Request("/recommend")
class MyRequest(BaseModel):
    user_id: int
    liked_items: list[str]
    disliked_items: list[str]

@Response("/recommend")
class MyResponse(BaseModel):
    recommendations: list[str]
    explanation: str
```

### Step 3: Implement the Wrapper

The wrapper is the only mandatory component. Subclass `DefaultHopwiseWrapper` and implement `distill` (input mapping) and `expand` (output rendering). Use `handle` to derive request-specific inference parameters.

```python
# your-plugin/your_processors.py
from hopwise.utils import PathLanguageModelingTokenType
from hoploy.registry import Wrapper
from hoploy.components import DefaultHopwiseWrapper

@Wrapper("my-wrapper")
class MyWrapper(DefaultHopwiseWrapper):
    def distill(self, request):
        """Translate request preferences into model-ready token sequences."""
        separator = self.dataset.path_token_separator
        bos = self.dataset.tokenizer.bos_token
        raw_inputs = []
        for item in request.liked_items:
            token = self.encode(item, PathLanguageModelingTokenType.ITEM.token)
            raw_inputs.append(separator.join([bos, token]))
        return raw_inputs

    def handle(self, request):
        """Derive request-specific inference parameters."""
        super().handle(request)
        return self

    def expand(self, values, request):
        """Convert generated paths into the application response."""
        scores, recommendations, explanations = values
        return {
            "recommendations": [self.decode(rec) for rec in recommendations],
            "explanation": [
                "".join([self.decode(t, real_token=True) for t in exp[1:]])
                for exp in explanations
            ],
        }
```

### Step 4: Implement Logits Processors (optional)

Logits processors guide or constrain token-level decoding during path generation. Multiple processors can be declared and are applied in declaration order.

```python
from hopwise.utils import PathLanguageModelingTokenType
from hoploy.registry import LogitsProcessor
from hoploy.components import RestrictedHopwiseLogitsProcessor

@LogitsProcessor("my-logits-processor")
class MyLogitsProcessor(RestrictedHopwiseLogitsProcessor):
    def handle(self, request):
        super().handle(request)
        if request.disliked_items:
            disliked_tokens = [
                self.encode(item, PathLanguageModelingTokenType.ITEM.token)
                for item in request.disliked_items
            ]
            self.set_restrictions(hard_restrictions=disliked_tokens)
        return self
```

### Step 5: Implement a Sequence Processor (optional)

The sequence processor receives complete generated paths and can apply post-generation operations such as filtering, duplicate removal, or re-ranking.

```python
from hoploy.registry import SequenceProcessor
from hoploy.components import DefaultHopwiseSequenceScorePostProcessor

@SequenceProcessor("my-sequence-processor")
class MySequenceProcessor(DefaultHopwiseSequenceScorePostProcessor):
    def handle(self, request):
        super().handle(request)
        return self
```

### Step 6: Configure your plugin

The plugin configuration file declares the components registered in the previous steps and binds them to API endpoints. It merges with the framework defaults at startup.

```yaml
# your-plugin/config.yaml
plugin:
  name: my-plugin
  wrapper: [my-wrapper]
  logits_processors: [my-logits-processor]
  sequence_processor: [my-sequence-processor]
  schema:
    module: your_schema
    get:
      info: my-wrapper.info
    post:
      recommend: run

wrapper:
  pearlm:
    name: my-wrapper
    device: "cuda:0"
    recommendation_count: 5
    diversity_factor: 0.5
    load_col_item: ["item_id", "name"]

logits_processors:
  my-logits-processor:
    name: my-logits-processor

sequence_processor:
  my-sequence-processor:
    name: my-sequence-processor
```

#### Component registration

`wrapper: [my-wrapper]`, `logits_processors: [my-logits-processor]`, and `sequence_processor: [my-sequence-processor]` tell the framework which implementations to load. Each value must match exactly the name passed to the corresponding decorator (`@Wrapper("my-wrapper")`, `@LogitsProcessor("my-logits-processor")`, `@SequenceProcessor("my-sequence-processor")`). Multiple logits processors can be listed and will be applied in declaration order.

The sections below (`wrapper.pearlm`, `logits_processors.my-logits-processor`, etc.) carry component-specific parameters. The model-level keys under `wrapper.pearlm` (`device`, `compile_mode`, `recommendation_count`, `diversity_factor`, etc.) depend on the underlying Hopwise model — refer to the [Hopwise documentation](https://github.com/tail-unica/hopwise) for the full list of supported parameters.

#### Schema and API routing

`schema.module` is the name of the Python file containing the schema definitions (without the `.py` extension). Hoploy imports this module at startup and discovers the `@Request` / `@Response` decorated classes automatically.

The `get` and `post` sections bind HTTP routes to handlers. Each key is the path suffix — it must match the path registered in the `@Request` / `@Response` decorators (e.g. `recommend` binds to `@Request("/recommend")`). Hoploy generates FastAPI routers for these endpoints automatically.

The value on the right-hand side selects the handler:

- **`run`** is a special keyword that passes the validated request body directly into the inference pipeline (distillation → path generation → expansion).
- **`component.method`** binds the route to a specific method on a registered component. For example, `info: my-wrapper.info` creates a `GET /info` endpoint backed by the `info()` method of `my-wrapper`. This is useful for auxiliary endpoints such as metadata inspection or item search. The method receives the request payload and may also access the catalog directly:

```python
from hoploy.core.catalog import get_catalog

@Wrapper("my-wrapper")
class MyWrapper(DefaultHopwiseWrapper):
    ...

    def info(self, request):
        catalog = get_catalog()
        item = catalog.get(request.item_id)
        return {"name": item["name"], "description": item["description"]}
```

### Step 7: Deploy

Add your plugin service to `compose.yaml`:

```yaml
services:
  your-plugin:
    <<: *hoploy-base
    ports:
      - "${YOUR_PLUGIN_PORT:-8100}:8100"
    volumes:
      - ./your-plugin:/app/plugin:ro
      - ./your-dataset-dir:/app/dataset:ro
      - ./your-checkpoints-dir:/app/checkpoints:ro
```

Then start the service:

```bash
docker compose up your-plugin
```

Each plugin runs as an independent Docker service built on a shared `hoploy-base` configuration.


https://github.com/user-attachments/assets/d6a78d46-0a5c-4c25-9b51-3a80968fafd9


from typing import List, Optional

from pydantic import BaseModel, Field, PositiveInt

from hoploy import Request, Response


# --- INFO: Request and Response Models ---


@Request("info")
class InfoRequest(BaseModel):
    """Request model for the course information endpoint."""

    item: str = Field(
        description="Name of the course to get information about",
        example="Learn Social Psychology",
    )


@Response("info")
class InfoResponse(BaseModel):
    """Response model for the course information endpoint.

    Every field except the course name is optional, since the catalog is
    built from the dataset files and individual attributes may be absent
    for some courses.
    """

    course: str = Field(
        description="Name of the course",
        example="Introduction to Political Psychology",
    )
    language: Optional[str] = Field(
        default=None, description="Language the course is taught in", example="English"
    )
    enrolments: Optional[float] = Field(
        default=None, description="Number of enrolments in the course", example=1210
    )
    price: Optional[float] = Field(
        default=None, description="Price of the course", example=89.99
    )
    objectives: Optional[List[str]] = Field(
        default=None,
        description="Learning objectives declared by the course",
        example=[
            "You will learn how unconscious processes guide our behaviour",
            "You will be able to describe the various universal moral intuitions that help us make moral decisions",
            "You will understand why most people do not change their minds when given facts",
            "You will be able to explain how personality traits impact our political ideologies",
        ],
    )


# --- SEARCH: Request and Response Models ---


@Request("search")
class SearchRequest(BaseModel):
    """Request model for the course search endpoint."""

    query: str = Field(
        description="Search query string",
        example="Introduction to Political Psychology",
    )
    limit: Optional[PositiveInt] = Field(
        default=10, description="Maximum number of results to return", example=5
    )
    max_price: Optional[float] = Field(
        default=None, description="Maximum price of the course", example=20.0
    )
    languages: Optional[List[str]] = Field(
        default=None,
        description="Languages the course may be taught in",
        example=["english", "portuguese"],
    )


@Response("search")
class SearchResponse(BaseModel):
    """Response model for the course search endpoint."""

    results: List[InfoResponse] = Field(
        description="List of courses matching the search criteria"
    )


# --- RECOMMENDATION: Request and Response Models ---


@Request("recommend")
class RecommendationRequest(BaseModel):
    """Request model for course recommendations."""

    # --- Identification and generation parameters ---

    user_id: str = Field(
        description=(
            "Opaque identifier of the requester, echoed back in the response. "
            "It is not resolved against the knowledge graph: recommendations "
            "are generated from the declared preferences, so the requester "
            "does not need to be a node of the graph."
        ),
        example="12345",
    )

    preferences: List[str] = Field(
        description="Courses the user has declared an interest in. Reasoning paths start from these courses.",
        example=["Learn Social Psychology", "An Introduction to Psychology"],
    )

    previous_recommendations: Optional[List[str]] = Field(
        default=None,
        description="Courses already recommended, excluded from the generated paths to avoid repetition",
        example=[
            "Introduction to Political Psychology",
            "International Politics Mastery: Levels of Analysis",
        ],
    )

    recommendation_count: Optional[PositiveInt] = Field(
        default=5, description="Number of recommendations to return", example=3
    )

    diversity_factor: Optional[float] = Field(
        default=0.5,
        description="Diversity penalty applied during path generation (0.0 to 1.0)",
        ge=0.0,
        le=1.0,
        example=0.7,
    )

    disable_collaborative: Optional[bool] = Field(
        default=False,
        description=(
            "If True, user nodes and the user-item relation are excluded from "
            "the generated paths, so explanations rely only on course "
            "properties. This constrains the form of the explanation; it does "
            "not remove collaborative signal from the underlying model."
        ),
        example=True,
    )

    # --- Knowledge graph filters ---
    #
    # These fields are consumed by the restricted logits processor of the
    # plugin. They must stay aligned with the entity types available in the
    # knowledge graph, and should not be advertised as active until that
    # processor is declared in the plugin configuration.

    concept_preferences: Optional[List[str]] = Field(
        default=None,
        description="Concepts the recommended courses should cover",
        example=["Psychology", "Local Area Network"],
    )

    category_preferences: Optional[List[str]] = Field(
        default=None,
        description="Categories the recommended courses should belong to",
        example=["Academics", "Personal Development"],
    )

    audience_preferences: Optional[List[str]] = Field(
        default=None,
        description="Target audiences the recommended courses should address",
        example=[
            "Street And Related Sales And Service Workers",
            "Teaching Professionals",
        ],
    )

    language_preferences: Optional[List[str]] = Field(
        default=None,
        description="Languages the recommended courses should be taught in",
        example=["English", "Spanish"],
    )

    level_preferences: Optional[List[str]] = Field(
        default=None,
        description="Difficulty levels the recommended courses should have",
        example=["Beginner", "Advanced"],
    )

    # instructor_preferences: Optional[List[str]] = Field(
    #     default=None,
    #     description="Instructors the recommended courses should be taught by",
    #     example=["Jan Zavrel", "Andrew Ng"],
    # )


class RecommendationItem(BaseModel):
    """Individual recommendation, with its score, explanation and metadata."""

    course: str = Field(
        description="Name of the recommended course",
        example="Learn Social Psychology Fundamentals",
    )
    score: float = Field(
        description="Recommendation score indicating suitability",
        example=0.95,
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Human-readable explanation derived from the reasoning path",
    )
    explanation_type: Optional[str] = Field(
        default=None,
        description=(
            "Pipe-separated sequence of relations traversed by the reasoning "
            "path, identifying the type of explanation produced"
        ),
        example="[UI-Relation]|[UI-Relation]",
    )
    metadata: Optional[InfoResponse] = Field(
        default=None, description="Additional information about the recommended course"
    )


@Response("recommend")
class RecommendationResponse(BaseModel):
    """Response model for course recommendations."""

    user_id: str = Field(
        description="Opaque identifier of the requester, echoed from the request",
        example="12345",
    )
    recommendations: List[RecommendationItem] = Field(
        description="Recommended courses with scores, explanations and metadata"
    )
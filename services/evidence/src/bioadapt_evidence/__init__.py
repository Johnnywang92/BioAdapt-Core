"""The evidence card service and the model-decision wiring built on it (plan V2.0 sections 4.5, 7.2-7.8, 11.5-11.6).

Three layers, in the order a reviewer reads them:

``assets`` reads a run's stored products and verifies that each index row still describes the bytes behind
it; ``cards`` derives what those measurements mean and files a draft card whose hypothesis is unknown;
``queue`` scores the drafts for the expert queue; ``outbound`` fixes which fields of a card may be shown
outside the data plane, and ``decision`` is the only path by which any of it can reach a model. The service
depends on the schema, policy and storage packages and on nothing else: it does not import a workflow, so a
second modality's products can be read by the same card (section 9.5).
"""

from .assets import (
    INTERMEDIATE_SLOTS,
    SCHEMA_BY_SLOT,
    SLOT_CLUSTERS,
    SLOT_COVERAGE,
    SLOT_DE_TABLE,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    SLOT_STABILITY,
    SLOT_STATE_AXIS,
    AssetReader,
    StoredProduct,
    barcodes_of,
    node_tool_of,
    specimen_of,
)
from .cards import (
    DEFAULT_BLOCKING_SHARE,
    CandidateCardBuilder,
    CardDraft,
    EvidenceCardService,
    ScreeningRule,
    SupportRule,
    card_id_for,
)
from .decision import (
    SYSTEM_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS_VERSION,
    EvidenceModelService,
    ModelCall,
    ModelCallRequest,
    ModelGateway,
    ModelResponse,
    UntrustedField,
)
from .errors import (
    CardUnderReview,
    ContextCompressionViolation,
    ContractDrift,
    EgressBlocked,
    EvidenceError,
    MissingProduct,
    ModelOutputRejected,
    ModelPathBlocked,
    ModelSideParameter,
    PromptUnsafe,
    TriggerOnHeldOutCells,
)
from .outbound import CARD_OUTBOUND, CardNarrative, card_model_request, card_outbound
from .queue import CardScore, ExpertQueue, build_queue, score_draft

__all__ = [
    "AssetReader",
    "CARD_OUTBOUND",
    "DEFAULT_BLOCKING_SHARE",
    "CardDraft",
    "CardNarrative",
    "CardScore",
    "CardUnderReview",
    "ContextCompressionViolation",
    "ContractDrift",
    "EvidenceCardService",
    "EvidenceError",
    "EvidenceModelService",
    "ExpertQueue",
    "INTERMEDIATE_SLOTS",
    "MissingProduct",
    "ModelCall",
    "ModelCallRequest",
    "ModelGateway",
    "ModelOutputRejected",
    "ModelPathBlocked",
    "ModelResponse",
    "ModelSideParameter",
    "PromptUnsafe",
    "SCHEMA_BY_SLOT",
    "SLOT_CLUSTERS",
    "SLOT_COVERAGE",
    "SLOT_DE_TABLE",
    "SLOT_MARKERS",
    "SLOT_QC_FLAGS",
    "SLOT_STABILITY",
    "SLOT_STATE_AXIS",
    "SYSTEM_INSTRUCTIONS",
    "SYSTEM_INSTRUCTIONS_VERSION",
    "ScreeningRule",
    "StoredProduct",
    "SupportRule",
    "TriggerOnHeldOutCells",
    "UntrustedField",
    "barcodes_of",
    "build_queue",
    "card_id_for",
    "card_model_request",
    "card_outbound",
    "node_tool_of",
    "score_draft",
    "specimen_of",
]

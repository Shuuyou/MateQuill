"""MateQuill's evidence-aware design planning and editable composition kernel."""
from .models import DesignBrief,DesignPlan,DesignConcept,DesignArtboard,DesignDocument
from .planner import plan_design,source_texts
from .compositor import compose_design
from .connectors import get_platform_connector,PlatformDesignConnector
from .renderer import render_design_svg

__all__=['DesignBrief','DesignPlan','DesignConcept','DesignArtboard','DesignDocument','plan_design','compose_design','get_platform_connector','PlatformDesignConnector','render_design_svg','source_texts']

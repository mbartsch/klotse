from copy import deepcopy

import pytest
from PySide6.QtCore import Qt

from klotse.k8s.resources import custom_columns, map_custom_object
from klotse.ui import karpenter_view as view
from klotse.ui.table_model import TableModel


CLASS = {
    "apiVersion": "karpenter.k8s.aws/v1", "kind": "EC2NodeClass", "metadata": {"name": "default"},
    "spec": {"amiFamily": "AL2023", "amiSelectorTerms": [{"alias": "al2023@v20260101"},
        {"name": "custom-*", "owner": "123", "tags": {"team": "platform"}}],
        "subnetSelectorTerms": [{"tags": {"karpenter.sh/discovery": "demo"}}],
        "securityGroupSelectorTerms": [{"id": "sg-123"}], "role": "node-role",
        "blockDeviceMappings": [{"deviceName": "/dev/xvda", "ebs": {"volumeSize": "80Gi", "encrypted": True}}],
        "metadataOptions": {"httpTokens": "required"}, "userData": "PRIVATE_BOOTSTRAP_CONTENT"},
    "status": {"amis": [{"id": "ami-123", "name": "image"}], "subnets": [{"id": "subnet-123", "zone": "zone-a"}],
        "securityGroups": [{"id": "sg-123", "name": "nodes"}], "instanceProfile": "profile",
        "conditions": [{"type": "Ready", "status": "True", "lastTransitionTime": "2026-01-02T12:00:00Z"},
                       {"type": "AMIsReady", "status": "True", "lastTransitionTime": "2026-01-02T14:00:00+01:00"}]} }


def test_ec2class_columns_report_configuration_and_condition_transition(qapp):
    row = map_custom_object(CLASS, group="karpenter.k8s.aws", kind="EC2NodeClass")
    assert row.ami_family == "AL2023"
    assert "alias=al2023@v20260101" in row.ami_selector
    assert "tags.team=platform" in row.ami_selector_tooltip and " OR " in row.ami_selector_tooltip
    assert row.amis == "ami-123" and row.ready == "✔ Ready"
    assert row.condition_changed == "2026-01-02 13:00:00 UTC"
    assert "not an object modification time" in row.condition_changed_tooltip
    columns = custom_columns("karpenter.k8s.aws", "EC2NodeClass")
    assert [col[0] for col in columns] == ["Name", "AMI family", "AMI selector", "Resolved AMIs", "Ready", "Last condition change", "Age"]
    model = TableModel(columns)
    model.set_rows([row])
    assert model.data(model.index(0, 2), Qt.ToolTipRole) == row.ami_selector_tooltip


def test_long_selectors_are_compact_but_full_tooltip_is_retained():
    raw = deepcopy(CLASS)
    raw["spec"]["amiSelectorTerms"] = [{"tags": {"team": "x" * 300}}]
    row = map_custom_object(raw, group="karpenter.k8s.aws", kind="EC2NodeClass")
    assert len(row.ami_selector) == 100 and row.ami_selector.endswith("…")
    assert "x" * 300 in row.ami_selector_tooltip


@pytest.mark.parametrize("conditions", [None, [], "bad", [{"lastTransitionTime": "invalid"}],
    [{"lastTransitionTime": "2026-01-01T00:00:00"}]])
def test_missing_or_invalid_transition_is_not_replaced_by_creation_time(conditions):
    row = map_custom_object({"metadata": {"creationTimestamp": "2020-01-01T00:00:00Z"}, "status": {"conditions": conditions}},
                            group="karpenter.k8s.aws", kind="EC2NodeClass")
    assert row.condition_changed == ""
    assert row.ami_family == row.ami_selector == row.amis == ""


def test_kind_detection_requires_known_group_and_supports_missing_gvk_mapper_metadata():
    assert view.kind_of(CLASS) == "EC2NodeClass"
    assert view.kind_of({"kind": "NodePool", "apiVersion": "example.com/v1"}) is None
    assert view.kind_of({}, "NodePool", "karpenter.sh") == "NodePool"
    assert view.kind_of({}, "NodePool", "example.com") is None
    assert view.kind_of({}) is None


def test_ec2class_summary_shows_resolved_resources_and_hides_user_data():
    page = view.render("EC2NodeClass", CLASS)
    for value in ("AL2023", "al2023@v20260101", "ami-123", "subnet-123", "sg-123", "node-role", "profile", "80Gi", "required",
                  "Ready", "Last transition", "2026-01-02 13:00"):
        assert value in page
    assert "PRIVATE_BOOTSTRAP_CONTENT" not in page and "inspect YAML" in page


def test_custom_resource_row_dispatches_to_the_karpenter_summary(qtbot):
    from klotse.ui.detail_panel import DetailPanel

    row = map_custom_object(CLASS, group="karpenter.k8s.aws", kind="EC2NodeClass")
    row.table_kind = "EC2NodeClass"
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(row)
    assert panel._formatted_ok
    assert "AL2023" in panel.formatted.toHtml()
    assert "PRIVATE_BOOTSTRAP_CONTENT" not in panel.formatted.toHtml()


def test_nodepool_summary_includes_allocation_requirements_and_disruption():
    raw = {"spec": {"weight": 0, "limits": {"cpu": "1000"}, "template": {"metadata": {"labels": {"team": "shop"}},
        "spec": {"nodeClassRef": {"name": "default", "kind": "EC2NodeClass"}, "expireAfter": "720h",
            "requirements": [{"key": "kubernetes.io/arch", "operator": "In", "values": ["arm64"], "minValues": 1}],
            "taints": [{"key": "dedicated", "effect": "NoSchedule"}]}},
        "disruption": {"consolidationPolicy": "WhenEmptyOrUnderutilized", "budgets": [{"nodes": "10%"}]}},
        "status": {"resources": {"cpu": "32", "nodes": "4"}}}
    page = view.render("NodePool", raw)
    for value in ("1000", "32", "arm64", "NoSchedule", "720h", "WhenEmptyOrUnderutilized", "10%", "shop"):
        assert value in page
    assert "klotse-goto:" in page and "EC2NodeClass" in page


def test_nodeclaim_summary_has_relationship_links_and_lifecycle_facts():
    raw = {"metadata": {"labels": {"karpenter.sh/nodepool": "pool", "node.kubernetes.io/instance-type": "m5.large",
        "topology.kubernetes.io/zone": "zone-a", "karpenter.sh/capacity-type": "spot"}, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "spec": {"nodeClassRef": {"name": "default"}, "resources": {"requests": {"cpu": "2"}}},
        "status": {"nodeName": "node-a", "providerID": "aws:///zone-a/i-123", "allocatable": {"memory": "4Gi"}}}
    page = view.render("NodeClaim", raw)
    for value in ("pool", "node-a", "m5.large", "spot", "zone-a", "i-123", "4Gi", "2026-01-01"):
        assert value in page
    assert "klotse-goto:" in page


@pytest.mark.parametrize("kind", list(view.GROUPS))
@pytest.mark.parametrize("raw", [{}, {"spec": None, "status": None}, {"spec": [], "status": "invalid"}])
def test_summaries_tolerate_absent_optional_fields(kind, raw):
    assert "Not reported" in view.render(kind, raw)


def test_resource_content_is_escaped_and_long_lists_are_bounded():
    raw = deepcopy(CLASS)
    raw["metadata"]["name"] = "<script>alert(1)</script>"
    raw["status"]["conditions"][0]["message"] = "<img src=x>"
    raw["status"]["amis"] = [{"id": f"ami-{i}"} for i in range(70)]
    page = view.render("EC2NodeClass", raw)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x>" not in page and "&lt;img src=x&gt;" in page
    assert "Showing 40 of 70" in page and "ami-69" not in page

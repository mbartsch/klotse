import yaml

from klotse.k8s import schema as sc

DOC = {"components": {"schemas": {
    "io.k8s.api.apps.v1.Deployment": {"type": "object", "x-kubernetes-group-version-kind": [{"group": "apps", "version": "v1", "kind": "Deployment"}],
                                      "properties": {"apiVersion": {"type": "string"}, "kind": {"type": "string"},
                                                     "metadata": {"$ref": "#/components/schemas/io.k8s.apimachinery.ObjectMeta"},
                                                     "spec": {"description": "Specification of the desired behavior.", "allOf": [{"$ref": "#/components/schemas/io.k8s.api.apps.v1.DeploymentSpec"}]}}},
    "io.k8s.api.apps.v1.DeploymentSpec": {"type": "object", "description": "DeploymentSpec is the specification.", "required": ["selector", "template"],
                                          "properties": {"replicas": {"type": "integer", "format": "int32", "description": "Number of desired pods. Defaults to 1."},
                                                         "paused": {"type": "boolean"}, "selector": {"type": "object", "properties": {"matchLabels": {"type": "object", "additionalProperties": {"type": "string"}}}},
                                                         "strategy": {"allOf": [{"$ref": "#/components/schemas/Strategy"}], "description": "How to replace pods."},
                                                         "template": {"$ref": "#/components/schemas/PodTemplate"}}},
    "Strategy": {"type": "object", "description": "type of strategy", "properties": {"type": {"type": "string", "enum": ["Recreate", "RollingUpdate"], "description": "Type of deployment."}}},
    "PodTemplate": {"type": "object", "properties": {"spec": {"$ref": "#/components/schemas/PodSpec"}}},
    "PodSpec": {"type": "object", "properties": {"containers": {"type": "array", "items": {"$ref": "#/components/schemas/Container"}, "description": "List of containers."}}},
    "Container": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "description": "Name of the container."},
                                                                         "image": {"type": "string", "description": "Container image name."},
                                                                         "args": {"type": "array", "items": {"type": "string"}}}},
    "io.k8s.apimachinery.ObjectMeta": {"type": "object", "properties": {"name": {"type": "string"}, "labels": {"type": "object", "additionalProperties": {"type": "string"}}}},
    "Free": {"type": "object", "x-kubernetes-group-version-kind": [{"group": "x.io", "version": "v1", "kind": "Free"}], "x-kubernetes-preserve-unknown-fields": True},
}}}

TEXT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  labels:
    app: web
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: app
        image: nginx
        args:
        - --x
      - name: side
        image: busybox
"""


def test_paths_come_from_the_manifest_or_a_table_of_builtin_kinds():
    assert sc.openapi_path("Deployment", "apps/v1") == "/openapi/v3/apis/apps/v1" and sc.openapi_path("Pod", "v1") == "/openapi/v3/api/v1"
    assert sc.openapi_path("Gateway", "gateway.networking.k8s.io/v1") == "/openapi/v3/apis/gateway.networking.k8s.io/v1"
    assert sc.openapi_path("Deployment") == "/openapi/v3/apis/apps/v1" and sc.openapi_path("ConfigMap") == "/openapi/v3/api/v1" and sc.openapi_path("Nothing") is None


def test_references_and_allof_are_followed_and_the_fields_own_description_wins():
    schema = sc.Schema(DOC)
    root = schema.root_for("Deployment", "apps/v1")
    assert "spec" in root["properties"] and schema.root_for("Deployment")["properties"] and schema.root_for("Nope") == {}
    spec = schema.walk(root, ["spec"])
    assert spec["description"] == "Specification of the desired behavior." and "replicas" in spec["properties"]  # the field's text over the type's
    assert schema.walk(root, ["spec", "strategy"])["description"] == "How to replace pods."
    assert schema.walk(root, ["spec", "template", "spec", "containers", "*", "image"])["description"] == "Container image name."
    assert schema.walk(root, ["spec", "nothing"]) is None and schema.walk(root, ["metadata", "labels", "any-key"])["type"] == "string"  # a map takes any key


def test_describe_gives_type_description_enum_required_and_children():
    schema = sc.Schema(DOC)
    root = schema.root_for("Deployment")
    replicas = schema.describe(root, ["spec", "replicas"])
    assert (replicas["type"], replicas["format"], replicas["required"]) == ("integer", "int32", False) and "Defaults to 1" in replicas["description"]
    assert schema.describe(root, ["spec", "selector"])["required"] is True
    assert schema.describe(root, ["spec", "strategy", "type"])["enum"] == ["Recreate", "RollingUpdate"]
    container = schema.describe(root, ["spec", "template", "spec", "containers", "*"])
    assert container["children"] == [("args", "array", False), ("image", "string", False), ("name", "string", True)]
    assert schema.describe(root, ["spec", "nothing"]) is None and schema.describe(root, [])["type"] == "object"


def test_fields_list_what_can_go_under_a_path_required_first():
    schema = sc.Schema(DOC)
    root = schema.root_for("Deployment")
    assert [f[0] for f in schema.fields(root, ["spec"])][:2] == ["selector", "template"]  # required first
    assert [f[0] for f in schema.fields(root, ["spec", "template", "spec", "containers"])] == ["name", "args", "image"]  # a list: its elements' fields
    assert schema.fields(root, ["metadata", "labels"]) == [] and schema.fields(root, ["nope"]) == []


def test_unknown_fields_are_found_but_free_form_parts_are_not_judged():
    schema = sc.Schema(DOC)
    root = schema.root_for("Deployment")
    data = yaml.safe_load(TEXT)
    assert schema.unknown_fields(data, root) == []
    data["spec"]["replicass"] = 1
    data["spec"]["template"]["spec"]["containers"][1]["imagee"] = "x"
    data["metadata"]["labels"]["whatever"] = "ok"  # a map: any key
    assert sorted(schema.unknown_fields(data, root)) == ["spec.replicass", "spec.template.spec.containers.[1].imagee"]
    assert schema.unknown_fields({"anything": {"goes": 1}}, schema.root_for("Free")) == []
    assert len(schema.unknown_fields({f"k{i}": 1 for i in range(50)}, root)) == 20


LINES = TEXT.splitlines()


def line_of(text):
    return next(i for i, line in enumerate(LINES) if line.strip() == text)


def test_the_path_of_a_line_comes_from_indentation_including_dashes_at_the_parents_indent():
    assert sc.yaml_path(LINES, line_of("replicas: 3"))[0] == ["spec", "replicas"]
    assert sc.yaml_path(LINES, line_of("image: nginx"))[0] == ["spec", "template", "spec", "containers", "*", "image"]
    assert sc.yaml_path(LINES, line_of("- --x"))[0] == ["spec", "template", "spec", "containers", "*", "args", "*"]
    assert sc.yaml_path(LINES, line_of("image: busybox"))[0] == ["spec", "template", "spec", "containers", "*", "image"]  # the second element
    assert sc.yaml_path(LINES, line_of("- name: side"))[0][-2:] == ["*", "name"] and sc.yaml_path(LINES, line_of("app: web"))[0] == ["metadata", "labels", "app"]
    assert sc.yaml_path(LINES, 0)[0] == ["apiVersion"] and sc.yaml_path([], 0)[0] == []
    indented = ["spec:", "  template:", "    spec:", "      containers:", "        - name: a", "          image: b"]  # dashes indented under the key
    assert sc.yaml_path(indented, 5)[0] == ["spec", "template", "spec", "containers", "*", "image"]
    assert sc.yaml_path(["a:", "  # note", "", "  b: 1"], 3)[0] == ["a", "b"]  # blanks and comments are skipped


def test_the_parent_and_the_siblings_of_a_line_being_typed():
    typing = LINES[: line_of("image: nginx")] + ["        im"] + LINES[line_of("image: nginx") + 1:]
    at = line_of("- name: app") + 1
    assert sc.parent_path(typing, at, 8) == ["spec", "template", "spec", "containers", "*"]
    assert sc.sibling_keys(typing, at, 8) == {"name", "args"}  # not the other container's keys, not the half-typed one
    assert sc.parent_path(["spec:", "  "], 1, 2) == ["spec"] and sc.sibling_keys(["spec:", "  replicas: 1", "  "], 2, 2) == {"replicas"}

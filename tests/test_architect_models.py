"""Tests for sciona.architect.models — CDG Pydantic models."""

import pytest

from sciona.architect.models import (
    AlgorithmicNode,
    AlgorithmicPrimitive,
    BaselineAnalyzerComponentSpec,
    BaselineAnalyzerSpec,
    BaselineComponentOutputRef,
    BaselineComponentShape,
    BaselinePredictorAliasSpec,
    BaselineStageSpec,
    BaselineWindowSpec,
    ConceptType,
    DependencyEdge,
    IOSpec,
    NodeStatus,
    SkeletonGraph,
)


class TestConceptType:
    def test_expected_members(self):
        expected = {
            "sorting",
            "searching",
            "divide_and_conquer",
            "greedy",
            "dynamic_programming",
            "graph_traversal",
            "graph_optimization",
            "string_matching",
            "geometry",
            "arithmetic",
            "number_theory",
            "combinatorics",
            "algebra",
            "optimization",
            "analysis",
            "set_theory",
            "signal_transform",
            "signal_filter",
            "graph_signal_processing",
            "neural_network",
            "clustering",
            "dimensionality_reduction",
            "ode_solver",
            "quadrature",
            "randomized",
            "information_theory",
            "compression",
            "sampler",
            "log_prob",
            "posterior_update",
            "variational_inference",
            "prior_init",
            "prior_distribution",
            "likelihood_evaluation",
            "probabilistic_oracle",
            "oracle_gradient",
            "mcmc_kernel",
            "mcmc_proposal",
            "vi_elbo",
            "sequential_filter",
            "smc_reweight",
            "message_passing",
            "conjugate_update",
            "fixed_point",
            "map_over",
            "baseline_analysis",
            "state_init",
            "data_assembly",
            "conditional_routing",
            "data_extraction",
            "visualization",
            "observability",
            "custom",
            "external_tool",
            "external_knowledge",
            "loss_function",
            "ml_model_selection",
        }
        assert {ct.value for ct in ConceptType} == expected


class TestIOSpec:
    def test_basic_creation(self):
        io = IOSpec(name="arr", type_desc="list[int]")
        assert io.name == "arr"
        assert io.type_desc == "list[int]"
        assert io.constraints == ""

    def test_with_constraints(self):
        io = IOSpec(name="arr", type_desc="list[int]", constraints="sorted, non-empty")
        assert io.constraints == "sorted, non-empty"

    def test_missing_required_field(self):
        with pytest.raises(Exception):
            IOSpec(name="x")  # type: ignore[call-arg]


class TestAlgorithmicNode:
    def test_minimal_creation(self):
        node = AlgorithmicNode(
            node_id="n1",
            name="Sort Array",
            description="Sort an array of integers",
            concept_type=ConceptType.SORTING,
        )
        assert node.node_id == "n1"
        assert node.parent_id is None
        assert node.status == NodeStatus.PENDING
        assert node.children == []
        assert node.depth == 0
        assert node.matched_primitive is None

    def test_full_creation(self):
        node = AlgorithmicNode(
            node_id="n2",
            parent_id="n1",
            name="Merge Step",
            description="Merge two sorted halves",
            concept_type=ConceptType.DIVIDE_AND_CONQUER,
            inputs=[IOSpec(name="left", type_desc="list[int]")],
            outputs=[IOSpec(name="merged", type_desc="list[int]")],
            status=NodeStatus.ATOMIC,
            children=["n3", "n4"],
            depth=2,
            type_signature="list[int] -> list[int] -> list[int]",
            matched_primitive="merge",
            critic_notes="Well-defined",
            decomposition_rationale="Standard merge operation",
        )
        assert node.parent_id == "n1"
        assert node.status == NodeStatus.ATOMIC
        assert len(node.inputs) == 1
        assert len(node.outputs) == 1
        assert node.children == ["n3", "n4"]

    def test_serialization_roundtrip(self):
        node = AlgorithmicNode(
            node_id="n1",
            name="Test",
            description="desc",
            concept_type=ConceptType.GREEDY,
            inputs=[IOSpec(name="x", type_desc="int")],
        )
        data = node.model_dump()
        restored = AlgorithmicNode.model_validate(data)
        assert restored == node


class TestDependencyEdge:
    def test_creation(self):
        edge = DependencyEdge(
            source_id="n1",
            target_id="n2",
            output_name="sorted",
            input_name="data",
            source_type="list[int]",
            target_type="list[int]",
        )
        assert edge.source_id == "n1"
        assert edge.target_id == "n2"
        assert edge.requires_glue is False

    def test_type_mismatch_with_glue(self):
        edge = DependencyEdge(
            source_id="n1",
            target_id="n2",
            output_name="result",
            input_name="data",
            source_type="list[float]",
            target_type="list[int]",
            requires_glue=True,
        )
        assert edge.requires_glue is True

    def test_serialization_roundtrip(self):
        edge = DependencyEdge(
            source_id="a",
            target_id="b",
            output_name="out",
            input_name="in",
            source_type="str",
            target_type="str",
        )
        data = edge.model_dump()
        restored = DependencyEdge.model_validate(data)
        assert restored == edge


class TestSkeletonGraph:
    def test_creation(self):
        sg = SkeletonGraph(
            paradigm=ConceptType.SORTING,
            name="Sorting",
            description="Compare-swap paradigm",
        )
        assert sg.template_nodes == []
        assert sg.template_edges == []
        assert sg.variants == []

    def test_with_nodes_and_edges(self):
        n1 = AlgorithmicNode(
            node_id="compare",
            name="Compare",
            description="Compare two elements",
            concept_type=ConceptType.SORTING,
        )
        n2 = AlgorithmicNode(
            node_id="swap",
            name="Swap",
            description="Swap elements",
            concept_type=ConceptType.SORTING,
        )
        edge = DependencyEdge(
            source_id="compare",
            target_id="swap",
            output_name="order",
            input_name="i",
            source_type="bool",
            target_type="int",
        )
        sg = SkeletonGraph(
            paradigm=ConceptType.SORTING,
            name="Sorting",
            description="desc",
            template_nodes=[n1, n2],
            template_edges=[edge],
            variants=["insertion_sort", "heapsort"],
        )
        assert len(sg.template_nodes) == 2
        assert len(sg.template_edges) == 1
        assert "insertion_sort" in sg.variants


class TestAlgorithmicPrimitive:
    def test_creation(self):
        prim = AlgorithmicPrimitive(
            name="heapsort",
            source="clrs-30",
            category=ConceptType.SORTING,
            description="Heapsort algorithm",
        )
        assert prim.name == "heapsort"
        assert prim.source == "clrs-30"
        assert prim.inputs == []
        assert prim.clrs_spec == {}

    def test_with_io(self):
        prim = AlgorithmicPrimitive(
            name="dijkstra",
            source="clrs-30",
            category=ConceptType.GRAPH_OPTIMIZATION,
            description="Single-source shortest paths",
            inputs=[
                IOSpec(name="graph", type_desc="weighted Graph"),
                IOSpec(name="source", type_desc="node"),
            ],
            outputs=[
                IOSpec(name="distances", type_desc="dict[node, float]"),
            ],
            type_signature="Graph -> Node -> Dict[Node, Float]",
            clrs_spec={"adj": "(input, node, pointer)"},
        )
        assert len(prim.inputs) == 2
        assert len(prim.outputs) == 1
        assert prim.type_signature != ""

    def test_serialization_roundtrip(self):
        prim = AlgorithmicPrimitive(
            name="test",
            source="test",
            category=ConceptType.CUSTOM,
            description="test prim",
        )
        data = prim.model_dump()
        restored = AlgorithmicPrimitive.model_validate(data)
        assert restored == prim


class TestBaselineAnalyzerModels:
    def test_windowed_component_requires_window_and_body(self):
        with pytest.raises(ValueError, match="windowed components require a window spec"):
            BaselineAnalyzerComponentSpec(
                name="irsqi",
                shape=BaselineComponentShape.WINDOWED,
                default_output_stage="windowed",
            )

    def test_combiner_component_requires_inputs_and_combine_stage(self):
        with pytest.raises(ValueError, match="combiner components require a combine_stage"):
            BaselineAnalyzerComponentSpec(
                name="combined",
                shape=BaselineComponentShape.COMBINER,
                default_output_stage="combine",
            )

    def test_component_references_must_resolve(self):
        with pytest.raises(ValueError, match="unknown stage 'missing'"):
            BaselineAnalyzerSpec(
                components=[
                    BaselineAnalyzerComponentSpec(
                        name="irsqi",
                        shape=BaselineComponentShape.WINDOWED,
                        window=BaselineWindowSpec(size=120, hop=120),
                        window_stages=[
                            BaselineStageSpec(
                                key="rise",
                                name="Exp Rise Step",
                            )
                        ],
                        default_output_stage="windowed",
                    ),
                    BaselineAnalyzerComponentSpec(
                        name="combined",
                        shape=BaselineComponentShape.COMBINER,
                        combine_stage=BaselineStageSpec(
                            key="combine",
                            name="Combine",
                            template_name="Combine",
                        ),
                        combine_inputs=[
                            BaselineComponentOutputRef(
                                component="irsqi",
                                stage_key="missing",
                            )
                        ],
                        default_output_stage="combine",
                    ),
                ]
            )

    def test_predictor_alias_references_resolve(self):
        spec = BaselineAnalyzerSpec(
            preprocessors=[
                BaselineStageSpec(
                    key="invert_pat",
                    name="Invert PAT Signal",
                    matched_primitive="baseline_invert_signal",
                )
            ],
            components=[
                BaselineAnalyzerComponentSpec(
                    name="patsqi",
                    shape=BaselineComponentShape.WINDOWED,
                    source_key="invert_pat",
                    window=BaselineWindowSpec(size=120, hop=120),
                    window_stages=[BaselineStageSpec(key="rise", name="Exp Rise Step")],
                    post_stages=[
                        BaselineStageSpec(
                            key="regions",
                            name="Regionize",
                            template_name="Regionize",
                            output_type="list[tuple[int,int]]",
                        )
                    ],
                    default_output_stage="regions",
                )
            ],
            predictor_aliases=[
                BaselinePredictorAliasSpec(
                    alias="pat",
                    source=BaselineComponentOutputRef(
                        component="patsqi",
                        stage_key="regions",
                    ),
                )
            ],
        )

        assert spec.predictor_aliases[0].alias == "pat"

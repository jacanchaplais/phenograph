"""
``graphicle.calculate``
=======================

Algorithms for performing common HEP calculations using graphicle data
structures.
"""
from typing import Tuple, Optional, Set, List, Dict
from functools import lru_cache, partial

import numpy as np
from numpy.lib.recfunctions import (
    unstructured_to_structured,
    structured_to_unstructured,
)
from typicle import Types
import networkx as nx
import pandas as pd

import graphicle as gcl


_types = Types()


def jet_mass(
    pmu: gcl.MomentumArray, weight: Optional[np.ndarray] = None
) -> float:
    """Returns the combined jet mass of the particles represented in
    the provided MomentumArray.

    Parameters
    ----------
    pmu : MomentumArray
        Momenta of particles comprising a jet, or an analagous combined
        object.
    weight : array, optional
        Weights for each particle when reconstructing the jet momentum.

    Notes
    -----
    This does not mask the MomentumArray for you. All filters and cuts
    must be applied before passing to this function.
    """
    eps = 1e-10
    data = structured_to_unstructured(pmu.data)
    if weight is not None:
        data = structured_to_unstructured(weight) * data
    minkowski = np.array([-1.0, -1.0, -1.0, 1.0])
    return np.sqrt(((data.sum(axis=0) ** 2) @ minkowski) + eps)  # type: ignore


def _diffuse(colors: List[np.ndarray], feats: List[np.ndarray]):
    color_shape = colors[0].shape
    av_color = np.zeros((color_shape[0], color_shape[1]), dtype="<f8")
    color_stack = np.dstack(colors)  # len_basis x feat_dim x num_in
    feat_stack = np.vstack(feats).T  # feat_dim x num_in
    feat_sum = np.sum(feat_stack, axis=1)
    nonzero_mask = feat_sum != 0.0
    av_color[:, nonzero_mask] = (
        np.sum(
            color_stack[:, nonzero_mask, :] * feat_stack[nonzero_mask], axis=2
        )
        / feat_sum[nonzero_mask]
    )
    return av_color


@lru_cache(maxsize=None)
def _trace_vector(
    nx_graph: nx.DiGraph,
    vertex: int,
    basis: Tuple[int, ...],
    feat_dim: int,
    is_structured: bool,
    exclusive: bool = False,
) -> np.ndarray:
    len_basis = len(basis)
    feat_fmt = structured_to_unstructured if is_structured else lambda x: x
    color = np.zeros((len_basis, feat_dim), dtype=_types.double)
    if vertex in basis:
        color[basis.index(vertex)] = 1.0
        if exclusive is True:
            return color
    in_edges = nx_graph.in_edges(vertex, data=True)
    colors_in: List[np.ndarray] = []
    feats = []
    for edge in in_edges:
        feats.append(feat_fmt(edge[2]["feat"]))
        in_vtx = edge[0]
        colors_in.append(
            _trace_vector(
                nx_graph, in_vtx, basis, feat_dim, is_structured, exclusive
            )
        )
    if colors_in:
        color += _diffuse(colors_in, feats)
    return color


def hard_trace(
    graph: gcl.Graphicle,
    mask: gcl.MaskArray,
    prop: np.ndarray,
    exclusive: bool = False,
    target: Optional[Set[int]] = None,
) -> Dict[str, np.ndarray]:
    """Performs flow tracing from specified particles in an event, back
    to the hard partons.

    Parameters
    ----------
    graph : Graphicle
        Full particle event, containing hard partons, showering and
        hadronisation.
    mask : MaskArray or MaskGroup
        Boolean mask identifying which particles should have their
        ancestry traced.
    prop : array
        Property to trace back, eg. 4-momentum, charge.
        Must be the same shape as arrays stored in graph.
        Can be structured, unstructured, or a graphicle array, though
        unstructured arrays must be 1d.
    exclusive : bool
        If True, double counting from descendant particles in the hard
        event will be switched off.
        eg. for event t > b W+, descendants of b will show no
        contribution from t, as b is a subset of t.
        Default is False.
    target : set of ints, optional
        Highlights specific partons in the hard event to decompose
        properties with respect to.
        If left as None, will simply use all partons in hard event,
        except for incoming partons.

    Returns
    -------
    trace_array : Dict of arrays
        Dictionary of arrays. Keys are parton names, arrays represent
        the contributions of hard partons traced down to the properties
        of the selected subset of particles specified by mask.
    """
    # encoding graph features onto NetworkX
    nx_graph = nx.DiGraph()
    graph_dict = graph.adj.to_dicts(edge_data={"feat": prop})
    example_feat = graph_dict["edges"][0][2]["feat"]
    try:
        feat_dim = len(example_feat)
        dtype = example_feat.dtype
    except TypeError:
        feat_dim = 1
        dtype = np.dtype(type(example_feat))
    is_structured = dtype.names is not None
    nx_graph.add_edges_from(graph_dict["edges"])
    # identify the hard ancestors to which we trace
    hard_mask = graph.hard_mask.copy()
    del hard_mask["incoming"]
    hard_graph = graph[hard_mask.bitwise_or]
    if target:  # restrict hard partons to user specified pdgs
        target_mask = hard_graph.pdg.mask(
            list(target), blacklist=False, sign_sensitive=True
        )
        hard_graph = hard_graph[target_mask]
    names, vtxs = tuple(hard_graph.pdg.name), tuple(hard_graph.edges["out"])
    # out vertices of user specified particles
    focus_pcls = graph.edges[mask]["out"]
    # struc_dtype = np.dtype(list(zip(names, ("<f8",) * len(names))))
    trc = np.array(
        [
            _trace_vector(nx_graph, pcl, vtxs, feat_dim, is_structured)
            for pcl in focus_pcls
        ]
    )
    _trace_vector.cache_clear()
    traces = dict()
    array_fmt = (
        partial(unstructured_to_structured, dtype=dtype)
        if is_structured
        else lambda x: x.squeeze()
    )

    for i, name in enumerate(names):
        traces[name] = array_fmt(trc[:, i, :])
    return traces
import os
import time
from logging import raiseExceptions

import numpy as np
import pandas as pd
import scipy.sparse as ssp
import scipy.stats as stats
import statsmodels.sandbox.stats.multicomp
from ete3 import Tree
from matplotlib import pyplot as plt
from scipy.cluster import hierarchy

# from plotnine import *
from sklearn.manifold import SpectralEmbedding

from cospar.tool import _utils as tl_util

from .. import help_functions as hf
from .. import logging as logg
from .. import settings


def fate_hierarchy(
    adata,
    selected_fates: list = None,
    source: str = "X_clone",
    selected_times: list = None,
    method: str = "SW",
):
    """
    Build fate hierarchy or lineage trees

    Parameters
    ----------
    source:
        Which information to use for hierarchy construction: 'X_clone' or any of the pre-computed transition map like 'transition_map'.

    Returns
    -------
    parent_map:
        A dictionary that returns the parent node for each child node.
        As an example:  {1: 4, 3: 4, 0: 5, 4: 5, 5: 6, 2: 6}
        In this simple map, node 1 and 3 are child of both 4, and node 0 and 4 are child of both 5 etc. In neighbor joining algorithm, typically you get a binary branching tree, so each parent only has two child node. Note that the last node '6' is the founder node, and this founder node by default includes all leaf node, and are not included in the node_groups
    node_groups:
        For each node (internal or leaf node), give its composition of all leaf nodes. As an example: {0: [0], 1: [1], 2: [2], 3: [3], 4: [1, 3], 5: [0, 1, 3]}.  5 is an internal node, and it composes [0,1,3], which are all leaf nodes.
    history:
        The history of the iterative reconstruction
    """

    if not (type(selected_fates) == list and len(selected_fates) > 0):
        raise ValueError("selected_fates must be a list with more than one elements")

    fate_N = len(selected_fates)
    X_history = []
    merged_pairs_history = []
    node_names_history = []
    node_groups = {i: [i] for i in range(fate_N)}

    parent_map = {}
    selected_fates_tmp = []
    for xx in selected_fates:
        if type(xx) is not list:
            xx = [xx]
        selected_fates_tmp.append(xx)
    node_names = list(range(fate_N))
    next_node = fate_N

    counter = 0
    while len(node_names) > 2:
        counter += 1
        fate_N_tmp = len(selected_fates_tmp)
        node_names_history.append(node_names)
        fate_coupling(
            adata,
            selected_fates=selected_fates_tmp,
            source=source,
            selected_times=selected_times,
            method=method,
            silence=True,
        )
        X_coupling = adata.uns[f"fate_coupling_{source}"]["X_coupling"]
        if counter == 1:
            fate_names = adata.uns[f"fate_coupling_{source}"]["fate_names"]

        X_history.append(np.array(X_coupling))
        floor = X_coupling.min() - 100
        for i in range(X_coupling.shape[0]):
            for j in range(X_coupling.shape[1]):
                if i >= j:
                    X_coupling[i, j] = floor

        ii = np.argmax(X_coupling.max(1))
        jj = np.argmax(X_coupling.max(0))
        merged_pairs_history.append((ii, jj))
        node_groups[next_node] = (
            node_groups[node_names[ii]] + node_groups[node_names[jj]]
        )

        parent_map[node_names[ii]] = next_node
        parent_map[node_names[jj]] = next_node

        ix = np.min([ii, jj])
        node_names = [
            n for n in node_names if not n in np.array(node_names)[np.array([ii, jj])]
        ]
        new_ix = np.array([i for i in range(fate_N_tmp) if not i in [ii, jj]])

        if len(new_ix) == 0:
            break
        new_fate = selected_fates_tmp[ii] + selected_fates_tmp[jj]
        selected_fates_tmp_1 = [selected_fates_tmp[new_ix[xx]] for xx in range(ix)]
        selected_fates_tmp_1.append(new_fate)
        for xx in range(ix, fate_N_tmp - 2):
            selected_fates_tmp_1.append(selected_fates_tmp[new_ix[xx]])
        selected_fates_tmp = selected_fates_tmp_1
        node_names.insert(ix, next_node)
        next_node += 1

    for i in node_names:
        parent_map[i] = next_node

    node_mapping = {}
    for key, value in node_groups.items():
        node_mapping[key] = [fate_names[xx] for xx in value]

    history = (X_history, merged_pairs_history, node_names_history)

    adata.uns[f"fate_hierarchy_{source}"] = {
        "parent_map": parent_map,
        "node_mapping": node_mapping,
        "history": history,
        "fate_names": fate_names,
    }
    logg.info(f"Results saved as dictionary at adata.uns['fate_hierarchy_{source}']")


def fate_coupling(
    adata,
    selected_fates=None,
    source="transition_map",
    selected_times=None,
    fate_map_method="sum",
    method="SW",
    silence=False,
):
    """
    Plot fate coupling determined by the transition map.

    We use the fate map :math:`P_i(\mathcal{C}_l)` towards a set of
    fate clusters :math:`\{\mathcal{C}_l, l=0,1,2...\}` to compute the
    fate coupling :math:`Y_{ll'}`.

    * If method='SW': we first obtain :math:`Y_{ll'}=\sum_i P_i(\mathcal{C}_l)P_i(\mathcal{C}_{l'})`.
      Then, we normalize the the coupling: :math:`Y_{ll'}\leftarrow Y_{ll'}/\sqrt{Y_{ll}Y_{l'l'}}`.

    * If method='Weinreb', we calculate the normalized
      covariance as in :func:`~cospar.tl.get_normalized_covariance`

    Parameters
    ----------
    adata: :class:`~anndata.AnnData` object
        Assume to contain transition maps at adata.uns.
    selected_fates: `list`, optional (default: all fates)
        List of cluster ids consistent with adata.obs['state_info'].
        It allows a nested list, where we merge clusters within
        each sub-list into a mega-fate cluster.
    source: `str`, optional (default: 'transition_map')
        Choices: {'X_clone', 'transition_map',
        'intraclone_transition_map',...}. If set to be 'clone', use only the clonal
        information. If set to be any of the precomputed transition map, use the
        transition map to compute the fate coupling. The actual available
        map depends on adata itself, which can be accessed at adata.uns['available_map']
    selected_times: `list`, optional (default: all)
        A list of time points to further restrict the cell states to plot.
        The default choice is not to constrain the cell states to show.
    fate_map_method: `str`, optional (default: 'sum')
        Method to obtain the fate probability map :math:`P_i(\mathcal{C})` towards a set
        of states annotated with fate :math:`\mathcal{C}`. Available options:
        {'sum', 'norm-sum'}. See :func:`.fate_map`.
        Plot the color bar.
    method: `str`, optional (default: 'SW')
        Method to normalize the coupling matrix: {'SW', 'Weinreb'}.

    Returns
    -------
    X_coupling and the corresponding fate_names index are stored as a dictionary
    at adata.uns[f"fate_coupling_{source}"]={"X_coupling": X_coupling, 'fate_names': fate_names}
    """

    hf.check_available_map(adata)
    time_info = np.array(adata.obs["time_info"])
    choices = list(adata.uns["available_map"]) + ["X_clone"]
    if source not in choices:
        raise ValueError(f"source should be among {choices}")
    elif source == "X_clone":
        sp_idx = hf.selecting_cells_by_time_points(time_info, selected_times)
        if np.sum(sp_idx) == 0:
            raise ValueError("No cells selected. Please change selected_times")

        else:
            # aggregrate cell states
            clone_annot = adata[sp_idx].obsm["X_clone"]
            state_annote = adata[sp_idx].obs["state_info"]
            (
                mega_cluster_list,
                __,
                __,
                sel_index_list,
            ) = hf.analyze_selected_fates(state_annote, selected_fates)
            if len(mega_cluster_list) == 0:
                raise ValueError("No cells selected. Computation aborted!")

            else:
                # coarse-grain the clonal matrix
                coarse_clone_annot = np.zeros(
                    (len(mega_cluster_list), clone_annot.shape[1])
                )
                for j, idx in enumerate(sel_index_list):
                    coarse_clone_annot[j, :] = clone_annot[idx].sum(0)

                X_coupling = tl_util.get_normalized_covariance(
                    coarse_clone_annot.T, method=method
                )
    else:
        cell_id_t1 = adata.uns["Tmap_cell_id_t1"]
        state_annote = adata.obs["state_info"]
        sp_idx = hf.selecting_cells_by_time_points(
            time_info[cell_id_t1], selected_times
        )

        (
            fate_map,
            mega_cluster_list,
            __,
            __,
            __,
            __,
            __,
        ) = tl_util.compute_fate_probability_map(
            adata,
            selected_fates=selected_fates,
            used_Tmap=source,
            map_backward=True,
            method=fate_map_method,
        )

        if (len(mega_cluster_list) == 0) or (np.sum(sp_idx) == 0):
            raise ValueError("No cells selected. Computation aborted!")

        else:
            X_coupling = tl_util.get_normalized_covariance(
                fate_map[sp_idx], method=method
            )

    adata.uns[f"fate_coupling_{source}"] = {
        "X_coupling": X_coupling,
        "fate_names": mega_cluster_list,
    }

    if not silence:
        logg.info(f"Results saved as dictionary at adata.uns['fate_coupling_{source}']")


def fate_map(
    adata,
    selected_fates=None,
    source="transition_map",
    map_backward=True,
    method="norm-sum",
    fate_count=False,
):
    """
    Plot transition probability to given fate/ancestor clusters.

    Given a transition map :math:`T_{ij}`, we explore build
    the fate map :math:`P_i(\mathcal{C})` towards a set of states annotated with
    fate :math:`\mathcal{C}` in the following ways.

    Step 1: Map normalization: :math:`T_{ij}\leftarrow T_{ij}/\sum_j T_{ij}`.

    Step 2: If `map_backward=False`, perform matrix transpose :math:`T_{ij} \leftarrow T_{ji}`.

    Step 3: aggregate fate probabiliteis within a given cluster :math:`\mathcal{C}`:

    * method='sum': :math:`P_i(\mathcal{C})=\sum_{j\in \mathcal{C}} T_{ij}`.
      This gives the intuitive meaning of fate probability.

    * method='norm-sum': We normalize the map from 'sum' method within a cluster, i.e.
      :math:`P_i(\mathcal{C})\leftarrow P_i(\mathcal{C})/\sum_j P_j(\mathcal{C})`.
      This gives the probability that a fate cluster :math:`\mathcal{C}` originates
      from an initial state :math:`i`.

    Parameters
    ----------
    adata: :class:`~anndata.AnnData` object
        Assume to contain transition maps at adata.uns.
    selected_fates: `list`, optional (default: all fates)
        List of cluster ids consistent with adata.obs['state_info'].
        It allows a nested list, where we merge clusters within
        each sub-list into a mega-fate cluster.
    source: `str`, optional (default: 'transition_map')
        The transition map to be used for plotting: {'transition_map',
        'intraclone_transition_map',...}. The actual available
        map depends on adata itself, which can be accessed at adata.uns['available_map']
    map_backward: `bool`, optional (default: True)
        If `map_backward=True`, show fate properties of initial cell states :math:`i`;
        otherwise, show progenitor properties of later cell states :math:`j`.
        This is used for building the fate map :math:`P_i(\mathcal{C})`. See :func:`.fate_map`.
    method: `str`, optional (default: 'norm-sum')
        Method to obtain the fate probability map :math:`P_i(\mathcal{C})` towards a set
        of states annotated with fate :math:`\mathcal{C}`. Available options:
        {'sum', 'norm-sum'}. See :func:`.fate_map`.
    fate_count: `bool`, optional (default: False)
        Used to determine the method for computing the fate potential of a state.
        If ture, just to count the number of possible fates; otherwise, use the Shannon entropy.

    Returns
    -------
    Fate map for each targeted fate cluster is updated at adata.obs[f'fate_map_{source}_{fate_name}'].
    The accompanying parameters are saved at adata.uns[f"fate_map_{source}_{fate}"]
    """

    hf.check_available_map(adata)

    if source not in adata.uns["available_map"]:
        raise ValueError(f"source should be among {adata.uns['available_map']}")

    else:
        if map_backward:
            cell_id_t1 = adata.uns["Tmap_cell_id_t1"]
        else:
            cell_id_t1 = adata.uns["Tmap_cell_id_t2"]

        if method == "norm-sum":
            color_bar_label = "Progenitor prob."
        else:
            color_bar_label = "Fate probability"

        (
            fate_map,
            mega_cluster_list,
            relative_bias,
            expected_prob,
            valid_fate_list,
            sel_index_list,
            fate_entropy,
        ) = tl_util.compute_fate_probability_map(
            adata,
            selected_fates=selected_fates,
            used_Tmap=source,
            map_backward=map_backward,
            method=method,
            fate_count=fate_count,
        )

        if len(mega_cluster_list) == 0:
            logg.error("No cells selected. Computation aborted!")
        else:
            for j, fate in enumerate(mega_cluster_list):
                temp_map = np.zeros(adata.shape[0]) + np.nan
                temp_map[cell_id_t1] = fate_map[:, j]
                adata.obs[f"fate_map_{source}_{fate}"] = temp_map
            adata.uns[f"fate_map_{source}"] = {
                "map_backward": map_backward,
                "method": method,
            }

            temp_map = np.zeros(adata.shape[0]) + np.nan
            temp_map[cell_id_t1] = fate_entropy
            adata.uns[f"fate_potency"] = temp_map

        logg.info(f"Results saved at adata.obs['fate_map_{source}_XXX']")


def fate_potency(
    adata,
    selected_fates=None,
    source="transition_map",
    map_backward=True,
    method="norm-sum",
    fate_count=False,
):
    """
    It quantifies how multi-potent a cell state is.

    If fate_count=True, it just to count the number of possible fates; otherwise, use the Shannon entropy.

    It runs :func:`.fate_map` to compute the fate potency. Please see all parameter definitions there.
    """
    fate_map(
        adata,
        selected_fates=selected_fates,
        source=source,
        map_backward=map_backward,
        method=method,
        fate_count=fate_count,
    )
    adata.obs[f"fate_potency_{source}"] = adata.uns["fate_potency"]
    adata.uns[f"fate_potency_{source}"] = {
        "map_backward": map_backward,
        "method": method,
    }
    logg.info(f"Results saved at adata.obs['fate_potency_{source}']")


def fate_bias(
    adata,
    selected_fates=None,
    source="transition_map",
    map_backward=True,
    method="norm-sum",
    sum_fate_prob_thresh=0.05,
    pseudo_count=0,
):
    """
    Plot fate bias to given two fate clusters (A, B).

    Given a fate map :math:`P_i` towards two fate clusters
    :math:`\{\mathcal{A}, \mathcal{B}\}`, constructed according
    to :func:`.fate_map`, we compute the fate bias of state :math:`i` as
    :math:`[P(\mathcal{A})+c_0]/[P(\mathcal{A})+P(\mathcal{B})+2c_0]`,
    where :math:`c_0=a * \max_{i,\mathcal{C}} P_i(\mathcal{C})`
    is a re-scaled pseudocount, with :math:`a` given by pseudo_count.

    Parameters
    ----------
    adata: :class:`~anndata.AnnData` object
        Assume to contain transition maps at adata.uns.
    selected_fates: `list`
        List of cluster ids consistent with adata.obs['state_info'].
        It allows a nested structure. If so, we merge clusters within
        each sub-list into a mega-fate cluster.
    source: `str`
        The transition map to be used for plotting: {'transition_map',
        'intraclone_transition_map',...}. The actual available
        map depends on adata itself, which can be accessed at adata.uns['available_map']
    map_backward: `bool`, optional (default: True)
        If `map_backward=True`, show fate properties of initial cell states :math:`i`;
        otherwise, show progenitor properties of later cell states :math:`j`.
        This is used for building the fate map :math:`P_i(\mathcal{C})`. See :func:`.fate_map`.
    method: `str`, optional (default: 'norm-sum')
        Method to obtain the fate probability map :math:`P_i(\mathcal{C})` towards a set
        of states annotated with fate :math:`\mathcal{C}`. Available options:
        {'sum', 'norm-sum'}. See :func:`.fate_map`.
    selected_times: `list`, optional (default: all)
        A list of time points to further restrict the cell states to plot.
        The default choice is not to constrain the cell states to show.
    sum_fate_prob_thresh: `float`, optional (default: 0.05)
        The fate bias of a state is plotted only when it has a cumulative fate
        probability to the combined cluster (A+B) larger than this threshold,
        i.e., P(i->A)+P(i+>B) >  sum_fate_prob_thresh.
    mask: `np.array`, optional (default: None)
        A boolean array for available cell states. It should has the length as adata.shape[0].
        Especially useful to constrain the states to show fate bias.
    plot_target_state: `bool`, optional (default: True)
        If true, highlight the target clusters as defined in selected_fates.
    color_bar: `bool`, optional (default: True)
        plot the color bar if True.
    show_histogram: `bool`, optional (default: True)
        If true, show the distribution of inferred fate probability.
    target_transparency: `float`, optional (default: 0.2)
        It controls the transparency of the plotted target cell states,
        for visual effect. Range: [0,1].
    figure_index: `str`, optional (default: '')
        String index for annotate filename for saved figures. Used to distinuigh plots from different conditions.
    pseudo_count: `float`, optional (default: 0)
        Pseudo count to compute the fate bias. See above.
    figure_title: `str`, optional (default: No title)

    Returns
    -------
    Results updated at adata.obs[f'fate_bias_{fate_1}_{fate_2}']
    """

    state_annote = adata.obs["state_info"]
    (
        mega_cluster_list,
        __,
        __,
        sel_index_list,
    ) = hf.analyze_selected_fates(state_annote, selected_fates)

    if len(mega_cluster_list) != 2:
        raise ValueError("selected_fates must have only two valid fates")

    fate_map(
        adata,
        selected_fates=selected_fates,
        source=source,
        map_backward=map_backward,
        method=method,
    )

    if pseudo_count == 0:
        pseudo_count = 10 ** (-10)

    if map_backward:
        cell_id_t1 = adata.uns["Tmap_cell_id_t1"]
    else:
        cell_id_t1 = adata.uns["Tmap_cell_id_t2"]

    fate_vector_1 = np.array(adata.obs[f"fate_map_{source}_{mega_cluster_list[0]}"])[
        cell_id_t1
    ]
    fate_vector_2 = np.array(adata.obs[f"fate_map_{source}_{mega_cluster_list[1]}"])[
        cell_id_t1
    ]
    add_count = pseudo_count * np.max([fate_vector_1, fate_vector_2])
    fate_vector_1 = fate_vector_1 + add_count
    fate_vector_2 = fate_vector_2 + add_count

    tot_prob = fate_vector_1 + fate_vector_2
    valid_idx = tot_prob > sum_fate_prob_thresh  # default 0.05
    fate_bias_vector = fate_vector_1[valid_idx] / (tot_prob[valid_idx])

    temp_map = np.zeros(adata.shape[0]) + 0.5  # initialize to be neutral
    temp_map[cell_id_t1[valid_idx]] = fate_bias_vector
    adata.obs[
        f"fate_bias_{source}_{mega_cluster_list[0]}*{mega_cluster_list[1]}"
    ] = temp_map

    adata.uns[f"fate_bias_{source}"] = {"map_backward": map_backward, "method": method}
    logg.info(f"Results saved at adata.obs['fate_bias_{source}']")


def differentiation_trajectory(
    adata,
    selected_fates=None,
    used_Tmap="transition_map",
    map_backward=True,
    method="norm-sum",
    selected_times=None,
    bias_threshold_A=0.5,
    bias_threshold_B=0.5,
    sum_fate_prob_thresh=0,
    pseudo_count=0,
    avoid_target_states=False,
    mask=None,
    plot_ancestor=True,
    savefig=False,
    plot_target_state=True,
    target_transparency=0.2,
):
    """
    Identify trajectories towards/from two given clusters.

    Given fate bias :math:`Q_i` for a state :math:`i` as defined in :func:`.fate_bias`,
    the selected ancestor population satisfies:

       * :math:`P_i(\mathcal{A})+P_i(\mathcal{B})` > sum_fate_prob_thresh;

       * Ancestor population for fate :math:`\mathcal{A}` satisfies :math:`Q_i` > bias_threshold_A

       * Ancestor population for fate :math:`\mathcal{B}` satisfies :math:`Q_i` < bias_threshold_B

    Parameters
    ----------
    adata: :class:`~anndata.AnnData` object
        Assume to contain transition maps at adata.uns.
    selected_fates: `list`
        List of cluster ids consistent with adata.obs['state_info'].
        It allows a nested structure.
    used_Tmap: `str`
        The transition map to be used for plotting: {'transition_map',
        'intraclone_transition_map',...}. The actual available
        map depends on adata itself, which can be accessed at adata.uns['available_map']
    map_backward: `bool`, optional (default: True)
        If `map_backward=True`, show fate properties of initial cell states :math:`i`;
        otherwise, show progenitor properties of later cell states :math:`j`.
        This is used for building the fate map :math:`P_i(\mathcal{C})`. See :func:`.fate_map`.
    method: `str`, optional (default: 'norm-sum')
        Method to obtain the fate probability map :math:`P_i(\mathcal{C})` towards a set
        of states annotated with fate :math:`\mathcal{C}`. Available options:
        {'sum', 'norm-sum'}. See :func:`.fate_map`.
    selected_times: `list`, optional (default: all)
        A list of time points to further restrict the cell states to plot.
        The default choice is not to constrain the cell states to show.
    bias_threshold_A: `float`, optional (default: 0), range: (0,1)
        The threshold for selecting ancestor population for fate A.
    bias_threshold_B: `float`, optional (default: 0), range: (0,1)
        The threshold for selecting ancestor population for fate B.
    sum_fate_prob_thresh: `float`, optional (default: 0), range: (0,1)
        Minimum cumulative probability towards joint cluster (A,B)
        to qualify for ancestor selection.
    pseudo_count: `float`, optional (default: 0)
        Pseudo count to compute the fate bias. The bias = (Pa+c0)/(Pa+Pb+2*c0),
        where c0=pseudo_count*(maximum fate probability) is a rescaled pseudo count.
    savefig: `bool`, optional (default: False)
        Save all plots.
    avoid_target_states: `bool`, optional (default: False)
        If true, avoid selecting cells at the target cluster (A, or B) as
        ancestor population.
    mask: `np.array`, optional (default: None)
        A boolean array for available cell states. It should has the length as adata.shape[0].
        Especially useful to constrain the states to show fate bias.
    plot_ancestor: `bool`, optional (default: True)
        If true, plot the progenitor states that have been selected based on cell fate bias.
    plot_target_state: `bool`, optional (default: True)
        If true, highlight the target clusters as defined in selected_fates.
    target_transparency: `float`, optional (default: 0.2)
        Transparency parameter for plotting.

    Returns
    -------
    adata.obs['cell_group_A']: `np.array` of `bool`
        A boolean array for selected progenitor states towards fate :math:`\mathcal{A}`.
    adata.obs['cell_group_B']: `np.array` of `bool`
        A boolean array for selected progenitor states towards fate :math:`\mathcal{B}`.
    adata.obs[f'traj_{fate_name}']: `np.array`
        A binary array for indicating states belonging to a trajectory.
    """

    diff_gene_A = []
    diff_gene_B = []
    hf.check_available_map(adata)
    fig_width = settings.fig_width
    fig_height = settings.fig_height
    point_size = settings.fig_point_size

    if used_Tmap not in adata.uns["available_map"]:
        logg.error(f"used_Tmap should be among {adata.uns['available_map']}")

    else:
        if map_backward:
            cell_id_t1 = adata.uns["Tmap_cell_id_t1"]
            cell_id_t2 = adata.uns["Tmap_cell_id_t2"]

        else:
            cell_id_t2 = adata.uns["Tmap_cell_id_t1"]
            cell_id_t1 = adata.uns["Tmap_cell_id_t2"]

        figure_path = settings.figure_path
        state_annote_t1 = np.array(adata.obs["state_info"][cell_id_t1])

        if len(selected_fates) != 2:
            logg.error(f" Must provide exactly two fates.")

        else:
            ## select time points
            time_info = np.array(adata.obs["time_info"])
            sp_idx = hf.selecting_cells_by_time_points(
                time_info[cell_id_t1], selected_times
            )
            if mask is not None:
                if len(mask) == adata.shape[0]:
                    mask = mask.astype(bool)
                    sp_idx = sp_idx & (mask[cell_id_t1])
                else:
                    logg.error(
                        "mask length does not match adata.shape[0]. Ignored mask."
                    )

            # if 'fate_map' not in adata.uns.keys():
            (
                fate_map,
                mega_cluster_list,
                relative_bias,
                expected_prob,
                valid_fate_list,
                sel_index_list,
                fate_entropy,
            ) = tl_util.compute_fate_probability_map(
                adata,
                selected_fates=selected_fates,
                used_Tmap=used_Tmap,
                map_backward=map_backward,
                method=method,
            )

            if (len(mega_cluster_list) != 2) or (np.sum(sp_idx) == 0):
                logg.error(
                    f"Do not have valid fates or time points. Computation aborted!"
                )
            else:
                if pseudo_count == 0:
                    pseudo_count = 10 ** (-10)

                potential_vector_temp = fate_map + pseudo_count * np.max(fate_map)
                valid_idx = fate_map.sum(1) > sum_fate_prob_thresh  # default 0.5

                diff = potential_vector_temp[:, 0]  # -potential_vector_temp[:,1]
                tot = potential_vector_temp.sum(1)

                # valid_idx=tot>sum_fate_prob_thresh # default 0
                valid_id = np.nonzero(valid_idx)[0]
                vector_array = np.zeros(np.sum(valid_idx))
                vector_array = diff[valid_idx] / (tot[valid_idx])

                idx_for_group_A = np.zeros(len(tot), dtype=bool)
                idx_for_group_B = np.zeros(len(tot), dtype=bool)
                idx_for_group_A[valid_id] = vector_array > (bias_threshold_A)
                idx_for_group_B[valid_id] = vector_array < (bias_threshold_B)

                ### remove states already exist in the selected fate cluster
                if avoid_target_states:
                    for zz in valid_fate_list[0]:
                        id_A_t1 = np.nonzero(state_annote_t1 == zz)[0]
                        idx_for_group_A[id_A_t1] = False

                    for zz in valid_fate_list[1]:
                        id_B_t1 = np.nonzero(state_annote_t1 == zz)[0]
                        idx_for_group_B[id_B_t1] = False

                group_A_idx_full = np.zeros(adata.shape[0], dtype=bool)
                group_A_idx_full[cell_id_t1[sp_idx]] = idx_for_group_A[sp_idx]
                group_B_idx_full = np.zeros(adata.shape[0], dtype=bool)
                group_B_idx_full[cell_id_t1[sp_idx]] = idx_for_group_B[sp_idx]
                adata.obs["cell_group_A"] = group_A_idx_full
                adata.obs["cell_group_B"] = group_B_idx_full

                if plot_ancestor:
                    x_emb = adata.obsm["X_emb"][:, 0]
                    y_emb = adata.obsm["X_emb"][:, 1]
                    state_annote = adata.obs["state_info"]

                    fig, nrow, ncol = start_subplot_figure(
                        2, row_height=4, n_columns=2, fig_width=8
                    )
                    ax = plt.subplot(nrow, ncol, 1)
                    customized_embedding(
                        x_emb, y_emb, group_A_idx_full, ax=ax, point_size=point_size
                    )
                    if plot_target_state:
                        for zz in valid_fate_list[0]:
                            idx_2 = state_annote == zz
                            ax.plot(
                                x_emb[idx_2],
                                y_emb[idx_2],
                                ".",
                                color="cyan",
                                markersize=point_size * 1,
                                alpha=target_transparency,
                            )
                    ax.set_title(f"Group A")
                    ax.axis("off")

                    ax = plt.subplot(nrow, ncol, 2)
                    customized_embedding(
                        x_emb, y_emb, group_B_idx_full, ax=ax, point_size=point_size
                    )
                    if plot_target_state:
                        for zz in valid_fate_list[1]:
                            idx_2 = state_annote == zz
                            ax.plot(
                                x_emb[idx_2],
                                y_emb[idx_2],
                                ".",
                                color="cyan",
                                markersize=point_size * 1,
                                alpha=target_transparency,
                            )
                    ax.set_title(f"Group B")
                    ax.axis("off")

                    plt.tight_layout()
                    if savefig:
                        fig.savefig(
                            f"{figure_path}/ancestor_state_groups.{settings.file_format_figs}"
                        )

                # diff_gene_A,diff_gene_B=differential_genes(adata,plot_groups=plot_groups,gene_N=gene_N,plot_gene_N=plot_gene_N,savefig=savefig,point_size=point_size)

                # store the trajectory
                temp_list = [group_A_idx_full, group_B_idx_full]
                for j, fate_name in enumerate(mega_cluster_list):
                    selected_idx = sel_index_list[j]
                    combined_prob_temp = temp_list[j].astype(int) + selected_idx.astype(
                        int
                    )
                    adata.obs[f"traj_{fate_name}"] = combined_prob_temp

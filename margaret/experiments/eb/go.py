import os
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import re
import scanpy as sc

from gprofiler import gprofiler
# Create a GOProfiler object


def transform_pval(p):
    return np.sqrt(-np.log(p))


def generate_go_terms(
    ad,
    de_key="rank_genes_groups",
    clusters_key="metric_clusters",
    lfc_cutoff=1.0,
    pval_cutoff=0.05,
    n_top=500,
    go_clusters=None,
    save_dir=None,
    **kwargs,
):
    de_res = ad.uns[de_key]
    communities = ad.obs[clusters_key]
    cluster_ids = np.unique(communities)
    query_cluster_ids = cluster_ids if go_clusters is None else go_clusters

    if save_dir is not None:
        os.makedirs(save_dir, exist_ok=True)

    for idx in query_cluster_ids:
        print(f"Computing GO terms for cluster: {idx}")

        # Read DE results
        names = de_res["names"][f"{idx}"]
        scores = pd.Series(de_res["scores"][f"{idx}"], index=names)
        lfc_vals = pd.Series(de_res["logfoldchanges"][f"{idx}"], index=names)
        adjp_vals = pd.Series(de_res["pvals_adj"][f"{idx}"], index=names)
        gene_index = lfc_vals.index

        # Keep values above threshold
        valid_lfc_inds = lfc_vals >= lfc_cutoff
        valid_pval_inds = adjp_vals <= pval_cutoff
        mask = valid_lfc_inds.multiply(valid_pval_inds)

        # Compute filtered genes
        remaining_genes = gene_index[mask]

        # Take the top genes from the filtered pool based on scores
        filtered_scores = scores.loc[remaining_genes]
        scores_ = filtered_scores.sort_values(ascending=False).iloc[:n_top]
        remaining_genes = scores_.index

        if len(remaining_genes) == 0:
            print(f"Found query size 0. Skipping GO analysis for cluster {idx}")
            continue

        # GO query
        print(f"Querying for {len(remaining_genes)} genes")
        go_df = gprofiler(organism="hsapiens", query=list(remaining_genes), **kwargs)
        if save_dir is not None:
            save_path = os.path.join(save_dir, f"GO_{idx}.csv")
            go_df.to_csv(save_path, index=False)
            print(f"GO terms for cluster: {idx} written at: {save_path}")


def filter_go_terms(terms_file_path, pat_file_path):
    # Read patterns and strip newline
    patterns = []
    with open(pat_file_path, "r") as fp:
        patterns = fp.readlines()
    patterns = [p.strip("\n") for p in patterns]

    # Read GO terms
    go_df = pd.read_csv(terms_file_path)
    go_df.index = go_df["native"]
    index = go_df.index
    name = go_df["name"]

    # Filter
    unique_ids = set()
    for pat in patterns:
        inds = index[name.str.match(pat, flags=re.IGNORECASE)]
        unique_ids = unique_ids.union(set(inds))

    filtered = go_df.loc[unique_ids]
    return filtered, list(unique_ids)


def generate_go_heatmap(
    term_paths,
    pattern_paths,
    save_filtered_df=True,
    save_path=None,
    save_kwargs={},
    order=None,
    groups=None,
    color_map=None,
    **kwargs,
):
    # Book-keeping
    cluster_labels = list(term_paths.keys())
    id_dict = {}
    pval_dict = {}
    unique_ids = set()
    big_df = pd.DataFrame()

    # Extract filtered GO terms for each cluster
    for cluster_id, term_path in term_paths.items():
        pat_path = pattern_paths[cluster_id]
        filtered_df, go_ids = filter_go_terms(term_path, pat_path)
        p_val = transform_pval(filtered_df["p_value"])
        unique_ids = unique_ids.union(set(go_ids))
        id_dict[cluster_id] = go_ids
        pval_dict[cluster_id] = p_val

        # Aggregate GO terms for saving
        save_columns = ["source", "native", "name", "p_value", "significant"]
        save_df = pd.DataFrame(index=filtered_df.index)
        save_df.loc[:, "lineage/cluster"] = cluster_id
        save_df.loc[:, save_columns] = filtered_df.loc[:, save_columns]
        big_df = pd.concat([big_df,save_df])

    print(big_df)

    if save_filtered_df:
        big_df.to_excel(
            os.path.join(save_path, f"GO_{len(term_paths)}.xlsx"),
            sheet_name="GO",
            index=False,
        )

    # Generate df for cluster vs GO terms found
    go_df = pd.DataFrame(index=cluster_labels, columns=unique_ids)
    for label in cluster_labels:
        go_df.loc[label, id_dict[label]] = pval_dict[label]

    go_df = go_df.fillna(0)

    # Create heatmap
    go_ann = sc.AnnData(go_df)
    go_ann.obs["clusters"] = go_ann.obs_names.astype("category")

    var_names = go_ann.var_names
    if order is not None:
        # Convert to string
        order_ = [str(o) for o in order]

        # Reorder and group GO terms according to order
        go_ann.obs["clusters"] = go_ann.obs["clusters"].cat.reorder_categories(order_)
        var_names = {}
        for o_, o in zip(order_, order):
            var_names[o_] = id_dict[o]

    if groups is not None:
        # Group according to a custom ordering
        var_names = {}
        for k, v in groups.items():
            vars = []
            for cluster_idx in v:
                vars.extend(id_dict[cluster_idx])
            var_names[k] = vars

    if color_map is not None:
        colors = [color_map[o] for o in order]
        go_ann.uns["clusters_colors"] = colors

    ax = sc.pl.heatmap(
        go_ann,
        var_names=var_names,
        groupby="clusters",
        show=False,
        **kwargs,
    )
    ax["groupby_ax"].set_ylabel("Clusters")

    # Save
    if save_path is not None:
        plt.savefig(os.path.join(save_path, f"GO_{len(term_paths)}.png"), **save_kwargs)
    plt.show()

    return ax

#!/usr/bin/env python3
"""Validate SVM and Factor-Analysis-based classifiers on Observer sentence corpus."""

from pathlib import Path
import json
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.preprocessing import Normalizer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.decomposition import TruncatedSVD, FactorAnalysis

BASE = Path("outputs_observer_full_corpus")
OUT = BASE / "model_validation"
OUT.mkdir(parents=True, exist_ok=True)


def report_to_df(y_true, y_pred):
    rep = classification_report(y_true, y_pred, output_dict=True, zero_division=0)
    df = pd.DataFrame(rep).T
    return df


def main():
    ds = pd.read_csv(BASE / "semantic_dataset.csv")
    ds = ds.dropna(subset=["sentence", "label"]).copy()

    # Keep higher-confidence pseudo-labels for cleaner validation signal
    ds = ds[ds["confidence"] >= 0.55].copy()

    # Ensure all classes have enough support
    counts = ds["label"].value_counts()
    keep_classes = counts[counts >= 40].index.tolist()
    ds = ds[ds["label"].isin(keep_classes)].copy()

    X = ds["sentence"].astype(str)
    y = ds["label"].astype(str)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 1) SVM model
    svm_pipe = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=50000, stop_words="english", sublinear_tf=True)),
        ("clf", LinearSVC(class_weight="balanced", random_state=42, max_iter=6000)),
    ])
    svm_pipe.fit(X_train, y_train)
    y_pred_svm = svm_pipe.predict(X_test)

    svm_report = report_to_df(y_test, y_pred_svm)
    svm_report.to_csv(OUT / "svm_classification_report.csv")

    labels = sorted(y.unique())
    cm_svm = confusion_matrix(y_test, y_pred_svm, labels=labels)
    pd.DataFrame(cm_svm, index=labels, columns=labels).to_csv(OUT / "svm_confusion_matrix.csv")

    # 5-fold CV on train split for robustness
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    svm_cv_f1_macro = cross_val_score(svm_pipe, X_train, y_train, cv=cv, scoring="f1_macro")

    # 2) FA + classifier model
    # Build dense latent representation from tfidf -> svd -> normalize -> factor analysis
    tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=35000, stop_words="english", sublinear_tf=True)
    X_train_tfidf = tfidf.fit_transform(X_train)
    X_test_tfidf = tfidf.transform(X_test)

    svd = TruncatedSVD(n_components=180, random_state=42, algorithm="arpack")
    X_train_svd = svd.fit_transform(X_train_tfidf)
    X_test_svd = svd.transform(X_test_tfidf)

    norm = Normalizer(copy=False)
    X_train_svd = norm.fit_transform(X_train_svd)
    X_test_svd = norm.transform(X_test_svd)

    scaler = StandardScaler()
    X_train_svd = scaler.fit_transform(X_train_svd)
    X_test_svd = scaler.transform(X_test_svd)

    fa = FactorAnalysis(n_components=30, random_state=42, max_iter=800)
    X_train_fa = fa.fit_transform(X_train_svd)
    X_test_fa = fa.transform(X_test_svd)

    fa_clf = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    fa_clf.fit(X_train_fa, y_train)
    y_pred_fa = fa_clf.predict(X_test_fa)

    fa_report = report_to_df(y_test, y_pred_fa)
    fa_report.to_csv(OUT / "fa_classification_report.csv")

    cm_fa = confusion_matrix(y_test, y_pred_fa, labels=labels)
    pd.DataFrame(cm_fa, index=labels, columns=labels).to_csv(OUT / "fa_confusion_matrix.csv")

    # simple CV estimate for FA pipeline using precomputed features on train split
    # (CV over classifier stage for computational efficiency)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    f1_scores_fa = []
    X_fa = X_train_fa
    y_np = y_train.to_numpy()
    for tr_idx, va_idx in skf.split(X_fa, y_np):
        clf = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
        clf.fit(X_fa[tr_idx], y_np[tr_idx])
        pred = clf.predict(X_fa[va_idx])
        f1_scores_fa.append(f1_score(y_np[va_idx], pred, average="macro"))

    summary = {
        "dataset_rows_after_conf_filter": int(len(ds)),
        "classes": labels,
        "class_counts": ds["label"].value_counts().to_dict(),
        "svm": {
            "test_accuracy": float(accuracy_score(y_test, y_pred_svm)),
            "test_f1_macro": float(f1_score(y_test, y_pred_svm, average="macro")),
            "test_f1_weighted": float(f1_score(y_test, y_pred_svm, average="weighted")),
            "cv_f1_macro_mean": float(np.mean(svm_cv_f1_macro)),
            "cv_f1_macro_std": float(np.std(svm_cv_f1_macro)),
        },
        "factor_analysis": {
            "test_accuracy": float(accuracy_score(y_test, y_pred_fa)),
            "test_f1_macro": float(f1_score(y_test, y_pred_fa, average="macro")),
            "test_f1_weighted": float(f1_score(y_test, y_pred_fa, average="weighted")),
            "cv_f1_macro_mean": float(np.mean(f1_scores_fa)),
            "cv_f1_macro_std": float(np.std(f1_scores_fa)),
            "svd_components": 180,
            "factor_components": 30,
            "svd_explained_variance_sum": float(np.sum(svd.explained_variance_ratio_)),
        },
    }

    # comparison table
    comparison = pd.DataFrame([
        {
            "model": "SVM (TF-IDF + LinearSVC)",
            "test_accuracy": summary["svm"]["test_accuracy"],
            "test_f1_macro": summary["svm"]["test_f1_macro"],
            "test_f1_weighted": summary["svm"]["test_f1_weighted"],
            "cv_f1_macro_mean": summary["svm"]["cv_f1_macro_mean"],
            "cv_f1_macro_std": summary["svm"]["cv_f1_macro_std"],
        },
        {
            "model": "FA latent factors + Multinomial LR",
            "test_accuracy": summary["factor_analysis"]["test_accuracy"],
            "test_f1_macro": summary["factor_analysis"]["test_f1_macro"],
            "test_f1_weighted": summary["factor_analysis"]["test_f1_weighted"],
            "cv_f1_macro_mean": summary["factor_analysis"]["cv_f1_macro_mean"],
            "cv_f1_macro_std": summary["factor_analysis"]["cv_f1_macro_std"],
        },
    ])
    comparison.to_csv(OUT / "model_comparison_summary.csv", index=False)

    with open(OUT / "validation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("Validation outputs written to", OUT)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

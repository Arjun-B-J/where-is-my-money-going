"""Offline evaluation of the categoriser against labelled data.

Until this package existed the project could say that every row was categorised
and that confidence spread out sensibly, but not whether the categories were
right. Those are different claims, and only the second one needs ground truth.

    dataset.py   the labelled rows, loaded through the production normaliser
    metrics.py   accuracy, calibration, abstention and latency from predictions
    runner.py    runs an arm (rules, model, or both) over the rows
    report.py    JSON results and the markdown comparison table

The evaluated code path is the production one: `classify_one` from the
pipeline and `RuleEngine` from the rules package. An eval that exercises a copy
of the classifier measures the copy.
"""

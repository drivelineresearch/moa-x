"""Native Muse text models, modes and provider-supported reasoning levels.

Model ids match Muse Code's model/list catalog, not its occasionally duplicated
human display labels. Historical route ids remain stable.
"""
MUSE_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
MUSE_ROUTES = {
    "muse-native": ("muse-spark-1.3", "Muse Spark 1.3"),
    "muse-native-contributor": ("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor"),
    "muse-wonder": ("muse-wonder-1.0", "Muse Wonder 1.0"),
    "muse-wonder-contributor": ("muse-wonder-1.0-contributor", "Muse Wonder 1.0 Contributor"),
    "muse-wonder-full": ("muse-wonder-full-1.0", "Muse Wonder Full 1.0"),
    "muse-wonder-full-contributor": ("muse-wonder-full-1.0-contributor", "Muse Wonder Full 1.0 Contributor"),
    "muse-spark-12": ("muse-spark-1.2", "Muse Spark 1.2"),
    "muse-spark-12-contributor": ("muse-spark-1.2-contributor", "Muse Spark 1.2 Contributor"),
}


def muse_efforts(model):
    # The provider rejects none/ultra despite the CLI accepting those flags.
    return list(MUSE_EFFORTS[:-1] if model.startswith("muse-spark-1.2") else MUSE_EFFORTS)

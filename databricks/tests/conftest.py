import sys
from pathlib import Path

import pytest
from pyspark.sql import SparkSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(scope="session")
def spark():
    s = (SparkSession.builder.master("local[2]").appName("docai-tests")
         .config("spark.sql.shuffle.partitions", "2").config("spark.ui.enabled", "false")
         .config("spark.driver.extraJavaOptions", "-Djava.security.manager=allow").getOrCreate())
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()

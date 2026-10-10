import csv
import io

from target import export


def test_csv_contents():
    response = export([{"project": "alpha,beta", "tokens": 15}])
    assert response["content_type"].split(";", 1)[0] == "text/csv"
    assert list(csv.DictReader(io.StringIO(response["body"]))) == [{"project": "alpha,beta", "tokens": "15"}]

import csv
import io


def export(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["project", "tokens"])
    writer.writeheader()
    writer.writerows([])
    return {"content_type": "text/csv; charset=utf-8", "body": output.getvalue()}

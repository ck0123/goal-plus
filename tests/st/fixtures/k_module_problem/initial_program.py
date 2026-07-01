def configure_pipeline():
    return {
        "loader": "json_reader",
        "preprocess": "dedupe",
        "algorithm": "mergesort",
        "formatter": "xml",
    }


if __name__ == "__main__":
    print(configure_pipeline())


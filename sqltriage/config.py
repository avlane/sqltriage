"""Optional TOML configuration.

    [connection]
    server = "SQLPROD01"
    trust_server_cert = true

    [sample]
    interval = 15
    top = 8

    [checks]
    only = ["waits", "blocking"]

    [agent]
    hours = 12

    [querystore]
    recent_hours = 2
    min_execs = 20
    ratio = 2.5

Command line flags always win over the file.
"""
import os
import tomllib

SEARCH_PATHS = [
    "sqltriage.toml",
    os.path.join(os.path.expanduser("~"), ".config", "sqltriage.toml"),
]

# section -> {key: argparse destination}
ARGUMENT_KEYS = {
    "connection": {
        "server": "server",
        "database": "database",
        "user": "user",
        "driver": "driver",
        "library": "library",
        "trust_server_cert": "trust_server_cert",
        "no_encrypt": "no_encrypt",
    },
    "sample": {"interval": "interval", "top": "top", "samples": "samples"},
    "output": {"format": "format", "min_severity": "min_severity", "fail_on": "fail_on"},
}

# section -> {key: Context.options name}
OPTION_KEYS = {
    "agent": {"hours": "agent_hours", "ignore": "ignore_jobs"},
    "blocking": {"min_wait_ms": "blocking_min_wait_ms"},
    "querystore": {
        "recent_hours": "qs_recent_hours",
        "lookback_hours": "qs_lookback_hours",
        "min_execs": "qs_min_execs",
        "ratio": "qs_ratio",
        "min_delta_ms": "qs_min_delta_ms",
        "top": "qs_top",
    },
}


class ConfigError(Exception):
    pass


def find(explicit=None):
    """The config file to use: the one asked for, else the first that exists."""
    if explicit:
        return explicit
    for path in SEARCH_PATHS:
        if os.path.isfile(path):
            return path
    return None


def load(path):
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError("cannot read {}: {}".format(path, exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("{}: {}".format(path, exc)) from exc
    check_keys(data, path)
    return data


def check_keys(data, path):
    known = {"checks": {"only", "databases", "exclude_databases"}, "weights": None}
    for section, keys in list(ARGUMENT_KEYS.items()) + list(OPTION_KEYS.items()):
        known[section] = set(keys)
    for section, values in data.items():
        if section not in known:
            raise ConfigError("{}: unknown section [{}]".format(path, section))
        if not isinstance(values, dict):
            raise ConfigError("{}: [{}] must be a table".format(path, section))
        if known[section] is None:  # free-form: the keys are check names, validated by the caller
            continue
        for key in values:
            if key not in known[section]:
                raise ConfigError("{}: unknown key '{}' in [{}]".format(path, key, section))


def argument_defaults(data):
    defaults = {}
    for section, keys in ARGUMENT_KEYS.items():
        for key, dest in keys.items():
            if key in data.get(section, {}):
                defaults[dest] = data[section][key]
    for key in ("only", "databases", "exclude_databases"):
        value = data.get("checks", {}).get(key)
        if value is not None:
            defaults[key] = ",".join(value)
    return defaults


def context_options(data):
    options = {}
    for section, keys in OPTION_KEYS.items():
        for key, name in keys.items():
            if key in data.get(section, {}):
                options[name] = data[section][key]
    if "weights" in data:
        options["weights"] = dict(data["weights"])
    return options

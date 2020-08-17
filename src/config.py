import toml
from src.paths import get_revolut_config_filepath, get_ynab_config_filepath


def load_ynab_config():
    path = get_ynab_config_filepath()
    config = toml.load(path)
    return config


def load_revolut_config():
    path = get_revolut_config_filepath()
    config = toml.load(path)
    return config


def get_revolut_account_config(account_alias):
    config = load_revolut_config()
    if account_alias not in config:
        raise ValueError(f"Account with alias {account_alias} not found in revolut.toml")
    return config[account_alias]


def get_ynab_account_config(account_alias):
    config = load_ynab_config()
    if account_alias not in config:
        raise ValueError(f"Account with alias {account_alias} not found in ynab.toml")
    return config[account_alias]

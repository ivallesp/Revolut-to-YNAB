import logging
import os
import ynab_client

from revolut import Revolut
import pandas as pd

from datetime import datetime

from src.config import get_ynab_account_config, get_revolut_account_config
from src.exceptions import (
    BudgetNotFoundError,
    AccountNotFoundError,
)

logger = logging.getLogger(__name__)


def update_ynab(revolut_account_alias):
    """Call the Revolut API with account name specified, download all the transactions,
    and bulk push them to YNAB through their API.

    Args:
        revolut_account_alias (str): Name of the Revolut account as configured in the
        config/Revolut.toml file.
    """
    revolut_conf = get_revolut_account_config(revolut_account_alias)
    ynab_account_alias = revolut_conf["ynab_account_alias"]
    ynab_current_account_name = revolut_conf["ynab_current_account_name"]
    ynab_conf = get_ynab_account_config(ynab_account_alias)
    budget_name = ynab_conf["budget_name"]
    transactions = download_revolut_transactions(revolut_account_alias)

    # Save the transactions for traceback purposes
    filename = datetime.now().isoformat() + "_" + revolut_account_alias + ".csv"
    path = os.path.join("logs", filename)
    pd.DataFrame(transactions).to_csv(path, sep=",", index=False)

    transactions = filter_revolut_transactions(transactions, config=revolut_conf)
    upload_revolut_transactions_to_ynab(
        transactions_revolut=transactions,
        budget_name=budget_name,
        ynab_current_account_name=ynab_current_account_name,
        ynab_account_alias=ynab_account_alias
    )


def filter_revolut_transactions(transactions, config):
    """
    This function is intended to be applied to the raw list of transactions provided by
    the revolut API.

    Args:
        transactions (list): list of dictionaries, one dict per transaction, as given by
        the revolut API.

    Returns:
        list: same format as the input transactions list but potentially shortened.
    """
    # Remove transactions in different currency
    logger.info(f"Received {len(transactions)} transactions to filter")
    currency = config["currency"]
    transactions = list(filter(lambda x: x["currency"] in currency, transactions))
    logger.info(
        f"{len(transactions)} transactions remaining after applying the "
        "currency filter!"
    )

    # Remove the temporary transactions. These transactions will disappear and be
    # replaced by permanent ones. If not removed, this causes duplicates in YNAB,
    # because they have different import IDs.
    filtered_types = ["DECLINED", "FAILED", "REVERTED"]
    transactions = list(
        filter(lambda x: x["state"] not in filtered_types, transactions)
    )
    logger.info(
        f"{len(transactions)} transactions remaining after applying the filter!"
    )
    return transactions


def download_revolut_transactions(account_alias):
    """Download all the Revolut transactions from the specified account

    Args:
        account_alias (str): Name of the Revolut account as configured in the
        config/revolut.toml file

    Raises:
        AuthenticationTimeoutError: if the user doesn't give acces through the mobile
        app (2-factor-auth), the function waits for 30 min and retries again. If the
        user does not respond after 5 trials, the function fails with this exception.

    Returns:
        list: transactions with the revolut native format
    """
    logger.info(f"Retrieving Revolut transactions from the account '{account_alias}'...")
    # Get access
    client = get_revolut_client(account_alias)
    # Get Revolut transactions
    logger.info("Requesting transfers to the Revolut API...")
    transactions = client.get_account_transactions().raw_list
    logger.info(f"{len(transactions)} transactions have been retrieved!")
    return transactions


def upload_revolut_transactions_to_ynab(
    transactions_revolut, budget_name, ynab_current_account_name, ynab_account_alias,
):
    """Gets a set of transactions as input and uploads them to the specified budget
    and account. It uses the bulk method for uploading the transactions to YNAB

    Args:
        transactions_revolut (list): list of dictionaries, Revolut native format
        budget_name (str): name of the budget as configured in the config/ynab.toml
        ynab_current_account_name (str): name of the current account existing in YNAB
        corresponding to a Revolut account as configured in the config/Revolut.toml
        ynab_account_alias (str): alias of the YNAB account configured into
        config/ynab.toml
    Raises:
        BudgetNotFoundError: this exception is raised when the budget specified does
        not exist in the YNAB account configured
        AccountNotFoundError: this exception is raised when the account specified does
        not exist in the specified budget of the YNAB account configured
    """
    logger.info(
        f"Requested {len(transactions_revolut)} transaction updates to budget "
        f"'{budget_name}' and account '{ynab_current_account_name}'"
    )
    # Get an instance of YNAB and Revolut APIs
    ynab_cli = get_ynab_client(ynab_account_alias)

    # Find the existing budgets and its respective IDs in YNAB
    ynab_budget_id_map = get_ynab_budget_id_mapping(ynab_cli)
    # If the budget name is not among the budget names retrieved, raise an exception
    if budget_name not in ynab_budget_id_map:
        budgets = list(ynab_budget_id_map.keys())
        budgets_str = "'" + "', '".join(budgets) + "'"
        raise BudgetNotFoundError(
            f"Budget named '{budget_name}' not found, available ones: {budgets_str}"
        )
    # Get the budget ID
    budget_id = ynab_budget_id_map[budget_name]
    logger.info(f"YNAB budget with name '{budget_name}' paired with id '{budget_id}'")

    # Find the existing accounts and its respective IDs in YNAB, within the budget
    ynab_account_id_map = get_ynab_account_id_mapping(ynab_cli, budget_id)
    # If the account name is not among the account names retrieved, raise an exception

    if ynab_current_account_name not in ynab_account_id_map:
        accounts = list(ynab_account_id_map.keys())
        accounts_str = "'" + "', '".join(accounts) + "'"
        raise AccountNotFoundError(
            f"YNAB account named '{ynab_current_account_name}' not found, available ones: {accounts_str}"
        )
    # Get the account ID
    account_id = ynab_account_id_map[ynab_current_account_name]
    logger.info(f"Account with name '{ynab_current_account_name}' paired with id '{account_id}'")
    logger.info(f"Translating transactions to YNAB format...")
    transactions_ynab = list(
        map(
            lambda t: _convert_revolut_transaction_to_ynab(t, account_id),
            transactions_revolut,
        )
    )
    logger.info(f"Requesting transactions push to the YNAB api...")
    transactions_ynab = ynab_cli.BulkTransactions(transactions=transactions_ynab)
    ynab_cli.TransactionsApi().bulk_create_transactions(budget_id, transactions_ynab)
    logger.info(f"Transactions pushed to YNAB successfully!")


def _convert_revolut_transaction_to_ynab(t_revolut, account_id):
    """Converts from the Revolut format to the YNAB format. Can be enhanced so that it
    translates from the Revolut automatic categorization to the YNAB one.

    Args:
        t_revolut (dict): dictionary containing all the rRvolut native transaction keys
        account_id (str): id of the YNAB account

    Returns:
        ynab_client.Transaction: transaction in the YNAB native format.
    """
    t_ynab = {
        "id": t_revolut["id"],
        "import_id": t_revolut["id"],
        "memo": t_revolut["description"],
        "account_id": account_id,
        "date": datetime.fromtimestamp(t_revolut["createdDate"] / 1000),
        "amount": int(t_revolut["amount"] - t_revolut["fee"]) * 10,
        "cleared": "uncleared",
        "approved": False,
        "deleted": False,
        "payee_name": t_revolut.get("merchant", {}).get("name", None),
    }
    t_ynab = ynab_client.TransactionWrapper(t_ynab)
    return t_ynab.transaction


def get_ynab_budget_id_mapping(ynab_client):
    """Build a mapping of YNAB budget names to internal ids

    Args:
        ynab_client (ynab_client): YNAB configured client with the credentials

    Returns:
        dict: Dictionary with budget names as keys and ids as values
    """
    response = ynab_client.BudgetsApi().get_budgets().data.budgets
    mapping = {budget.name: budget.id for budget in response}
    return mapping


def get_ynab_account_id_mapping(ynab_client, budget_id):
    """Build a mapping of YNAB account names to internal ids

    Args:
        ynab_client (ynab_client): YNAB configured client with the credentials
        budget_id (str): id of the budget to query

    Returns:
        dict: Dictionary with account names as keys and ids as values
    """
    response = ynab_client.AccountsApi().get_accounts(budget_id).data.accounts
    mapping = {account.name: account.id for account in response}
    return mapping


def get_ynab_client(account_alias):
    """Handles YNAB connection and returns the cli

    Args:
        account_alias (str): Name of the Revolut account as configured in the
        config/revolut.toml file

    Returns:
        ynab_client: client ready to query the API
    """
    config = get_ynab_account_config(account_alias)
    configuration = ynab_client.Configuration()
    configuration.api_key_prefix["Authorization"] = "Bearer"
    configuration.api_key["Authorization"] = config["api_key"]
    return ynab_client


def get_revolut_client(revolut_account_alias):
    """Handles the Revolut connection and returns the cli

    Args:
        revolut_account_alias (str): name of the YNAB account associated to a Revolut account
        as configured in the config/Revolut.toml

    Returns:
        revolut.Revolut: client ready to query the API
    """
    config = get_revolut_account_config(revolut_account_alias)
    client = Revolut(device_id=config["device_id"], token=config["token"])
    return client

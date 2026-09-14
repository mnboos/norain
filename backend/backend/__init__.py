import os
from pathlib import Path

import dotenv
from loguru import logger


def load_dotenv(dotenv_default_path: str = "../.env") -> None:
    """
    Load the environment variables from the .env file specified in ENV_FILE.

    :return:
    """

    vars_before = {**os.environ}
    _env_file: str = os.environ.get("ENV_FILE", dotenv_default_path)
    if not _env_file:
        _env_file = ".env"
        logger.warning("ENV_FILE is not set and was defaulted to '.env")

    for i, env_file in enumerate(_env_file.split(",")):
        env_file_path = verify_path(env_file)

        is_not_first_file = bool(i)
        logger.info("Loading dotenv: {}", env_file_path)
        anything_changed = dotenv.load_dotenv(env_file_path, override=is_not_first_file, interpolate=True)
        if anything_changed:
            values = dotenv.dotenv_values(env_file_path)
            django_settings_module = values.get("DJANGO_SETTINGS_MODULE")
            if django_settings_module:
                os.environ["DJANGO_SETTINGS_MODULE"] = django_settings_module

            if os.environ.get("ENV_FILE_LOG_CHANGES", "False") == "True":
                log_changes(vars_before)

            dotenv_template_lookup_dirs = [".", ".."]
            for p in dotenv_template_lookup_dirs:
                dotenv_template = Path(p) / ".env.template"
                notify_diff(env_file_path, dotenv_template)

            logger.info("dotenv loaded: {}", env_file_path)
        else:
            logger.info("Loading dotenv didn't change any variable: {}", env_file_path)


def log_changes(vars_before: dict) -> None:
    """
    Print the variables that are different compared to vars_before.

    :param vars_before:
    :return:
    """

    vars_set = [v for v in os.environ if v not in vars_before]
    vars_changed = [v for v in os.environ if v in vars_before and os.environ[v] != vars_before[v]]
    for variable in sorted(os.environ.keys()):
        if variable in vars_set or vars_changed:
            val = os.environ[variable]
            if val and any(exclude in variable for exclude in ["SECRET", "PASSWORD"]):
                val = "*******"
            logger.info("{}={}", variable, val)


def verify_path(env_file: str) -> Path:
    """
    Check that the file exists and returns a Path object to it.
    """

    env_file_path = Path(env_file)
    if not env_file_path.exists():
        logger.error(f"dotenv file not found: {env_file}")
        msg = f"dotenv file not found: {env_file}"
        raise RuntimeError(msg)
    if not env_file_path.is_file():
        if env_file_path.is_dir():
            msg = (
                f"dotenv file is not a file but a directory. This might be a docker bind-mount problem."
                f" Check all the mounted paths and files: {env_file}"
            )
            logger.error(msg)
            raise RuntimeError(msg)
        logger.error(f"dotenv file is not a file: {env_file}")
        msg = f"dotenv file is not a file: {env_file}"
        raise RuntimeError(msg)
    return env_file_path.resolve()


def notify_diff(dotenv_path: Path, dotenv_template_path: Path) -> None:
    """
    Print difference between configured .env and .env.template.

    :param dotenv_path:
    :param dotenv_template_path:
    :return:
    """

    if dotenv_template_path.is_file():
        template_env_keys = set(dotenv.dotenv_values(dotenv_template_path).keys())
        env_keys = set(dotenv.dotenv_values(dotenv_path).keys())
        deprecated = env_keys - template_env_keys
        missing = template_env_keys - env_keys
        if deprecated:
            logger.warning(
                "'{}' contains deprecated keys not existing in .env.template: {}",
                dotenv_path,
                ", ".join(deprecated),
            )
        if missing:
            logger.warning(
                "'{}' is missing the following keys existing in .env.template: {}",
                dotenv_path,
                ", ".join(missing),
            )

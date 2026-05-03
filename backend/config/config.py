import os
import yaml

config_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(config_dir, "api_config.yaml")

with open(config_path, 'r', encoding='utf-8') as f:
    API_CONFIG = yaml.safe_load(f)

import yaml
import os
import logging

def load_config(config_path='config.yaml'):
    """Load configuration from YAML file"""
    try:
        if not os.path.exists(config_path):
            logging.warning(f"Config file {config_path} not found. Using default settings.")
            return None
            
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
            logging.info(f"Config loaded successfully from {config_path}")
            return config
    except Exception as e:
        logging.error(f"Error loading config from {config_path}: {str(e)}")
        return None

def save_config(config, config_path='config.yaml'):
    """Save configuration to YAML file"""
    try:
        with open(config_path, 'w') as file:
            yaml.dump(config, file, default_flow_style=False)
            logging.info(f"Config saved successfully to {config_path}")
        return True
    except Exception as e:
        logging.error(f"Error saving config to {config_path}: {str(e)}")
        return False

# Usage example:
if __name__ == "__main__":
    config = load_config()
    if config:
        print("Configuration loaded successfully!")
        # Access config values
        print(f"Telegram API Key: {config['telegram']['api_key']}")
        print(f"Update interval: {config['schedule']['interval']} hours")
    else:
        print("Failed to load configuration.")

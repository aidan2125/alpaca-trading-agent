import os
import requests
import logging

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Set up logging
logger = logging.getLogger(__name__)

def send_discord_message(message: str, timeout=10):
    """
    Sends a message to Discord using a webhook.
    
    Args:
        message: The message to send
        timeout: Request timeout in seconds
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not DISCORD_WEBHOOK_URL:
        logger.error("Discord alert failed: no webhook URL configured")
        print("⚠️  Discord alert failed: no webhook URL configured.")
        return False
    
    # Discord has a 2000 character limit
    if len(message) > 2000:
        logger.warning(f"Discord message truncated from {len(message)} to 2000 chars")
        message = message[:1997] + "..."
    
    payload = {"content": message}
    
    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL, 
            json=payload,
            timeout=timeout
        )
        
        if response.status_code == 204:
            logger.info("Discord alert sent successfully")
            return True
        else:
            logger.error(f"Discord send failed (status {response.status_code}): {response.text}")
            print(f"❌ Discord send failed (status {response.status_code})")
            return False
            
    except Exception as e:
        logger.error(f"Discord alert failed: {e}")
        print(f"❌ Discord alert failed: {e}")
        return False
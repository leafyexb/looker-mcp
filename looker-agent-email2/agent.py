import os
import json
import email.message
import base64
import logging
import requests
from urllib.parse import urlparse

from google.adk.agents import LlmAgent
from google.adk.planners.built_in_planner import BuiltInPlanner
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.models import LiteLlm
from google.genai.types import ThinkingConfig
import google.auth.transport.requests
import google.oauth2.id_token
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.readonly_context import ReadonlyContext
from vertexai.agent_engines import AdkApp
import ssl
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)

# Replace this URL with the correct endpoint for your MCP server.
#MCP_SERVER_URL = "https://toolbox-YOUR_PROJECT_NUMBER.us-central1.run.app/mcp"
MCP_SERVER_URL = "https://looker.mytest.local/mcp"
OAUTH_TOKEN_STATE_KEY = "gmail-user-id-12345"

def get_headers(tool_context: ToolContext) -> dict:
    """Retrieve OAuth headers from the tool context state."""
    oauth_token = "NOT_FOUND"
    
    # 1. Try our hardcoded state key first
    oauth_token = tool_context.state.get(OAUTH_TOKEN_STATE_KEY, oauth_token)
    
    # 2. Fallback: Dynamically search for any active Google OAuth token (starts with 'ya29.')
    if oauth_token == "NOT_FOUND":
        for key, val in tool_context.state.items():
            if isinstance(val, str) and val.startswith("ya29."):
                logging.info(f"Dynamically discovered Google OAuth token in session state under key: '{key}'")
                oauth_token = val
                break
                
    if oauth_token == "NOT_FOUND":
        logging.warning("No Gmail OAuth token was found in OAUTH_TOKEN_STATE_KEY or via dynamic 'ya29.' state scanning.")
        logging.debug(f"Available session state keys: {list(tool_context.state.keys())}")

    headers = {
        "Authorization": f"Bearer {oauth_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    return headers

def create_raw_message(sender: str, to: str, subject: str, body: str) -> str:
    """Create a MIME RFC 2822 email message and encode it to Base64 URL-safe format."""
    msg = email.message.EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    msg.set_content(body)

    # Get raw bytes and encode to base64 URL-safe (stripping trailing '=' padding)
    raw_bytes = msg.as_bytes()
    encoded_message = base64.urlsafe_b64encode(raw_bytes).decode('utf-8').rstrip('=')
    return encoded_message

def send_email(
    message: str,
    tool_context: ToolContext,
    recipient: str = None,
) -> str:
    """
    Sends an email with the Looker results.
    Defaults to sending the email to the user's own retrieved email address if a recipient is not specified.
    """
    headers = get_headers(tool_context)
    if "NOT_FOUND" in headers.get("Authorization", ""):
        return "Error: Gmail OAuth token was not found in the tool context state. Please authenticate first."
    
    email_url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    send_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    try:
        # Get the profile email address
        response = requests.get(email_url, headers=headers)
        response.raise_for_status()
        email_address = response.json()['emailAddress']

        # Determine target recipient (use prompt recipient if provided, else user's own email)
        target_recipient = recipient if recipient else email_address

        raw_string = create_raw_message(
            email_address,
            target_recipient,
            "Looker Results",
            message
        )
        data = {
            "raw": raw_string
        }
        
        send_response = requests.post(send_url, headers=headers, json=data)
        send_response.raise_for_status()
        
        logging.info(f"Email successfully sent to {target_recipient}")
        return f"Successfully sent the email to {target_recipient}."
    except Exception as e:
        # Dynamically inspect active token scopes and the raw Gmail API response to help with troubleshooting
        token_scopes = "Unknown"
        gmail_error_body = "No response body available"
        if hasattr(e, 'response') and e.response is not None:
            try:
                gmail_error_body = e.response.text.strip()
            except Exception:
                pass
                
        try:
            auth_header = headers.get("Authorization", "")
            active_token = auth_header.split("Bearer ")[1].strip() if "Bearer " in auth_header else ""
            if active_token and active_token != "NOT_FOUND":
                # We fetch the active scopes of the token securely (without printing the raw token)
                tokeninfo_response = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={active_token}")
                if tokeninfo_response.status_code == 200:
                    token_scopes = tokeninfo_response.json().get("scope", "None")
                else:
                    token_scopes = f"HTTP {tokeninfo_response.status_code} (Error: {tokeninfo_response.text.strip()})"
            else:
                token_scopes = "No valid OAuth token in request headers"
        except Exception as scope_err:
            token_scopes = f"Check Exception: {scope_err}"
            
        # Safe fallback target recipient name for logging
        fallback_recipient = recipient if recipient else "me"
        
        logging.error(f"Failed to send email to {fallback_recipient} (Active Scopes: {token_scopes}, Response: {gmail_error_body}): {e}", exc_info=True)
        return (
            f"Failed to send email to {fallback_recipient} due to a security/permission error: {e}.\n"
            f"📥 Gmail API Error Response: {gmail_error_body}\n"
            f"🔑 Active scopes in your session token: [{token_scopes}].\n"
            f"👉 Please verify that 'https://www.googleapis.com/auth/gmail.send' is explicitly configured and authorized in your Gemini Extension OAuth panel."
        )

if not MCP_SERVER_URL:
    raise ValueError("The MCP_SERVER_URL is not set.")

def get_id_token() -> str | None:
    """Get an ID token to authenticate with the MCP server (Cloud Run)."""
    target_url = MCP_SERVER_URL
    try:
        # Robustly parse the service base URL to use as the GCP audience
        parsed_url = urlparse(target_url)
        audience = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        auth_req = google.auth.transport.requests.Request()
        id_token = google.oauth2.id_token.fetch_id_token(auth_req, audience)

        if not id_token:
            logging.error("fetch_id_token returned None or an empty string.")
            return None

        logging.info(f"Successfully fetched ID token ending with: ...{id_token[-6:] if id_token else 'N/A'}")
        return id_token
    except Exception as e:
        logging.error(f"Error fetching ID token: {e}", exc_info=True)
        return None

def dynamic_header_provider(context: ReadonlyContext) -> dict:
    """
    Fetches a new ID token and returns the Authorization header.
    This function is called before each tool invocation.
    """
    id_token = get_id_token()
    headers = {}
    if id_token:
        headers["Authorization"] = f"Bearer {id_token}"
    else:
        logging.warning("No ID token fetched, Authorization header will be missing.")
    return headers

def custom_httpx_client_factory(**kwargs):
  ssl_context = ssl.create_default_context()
  # Example: Load a custom CA bundle
  # ssl_context.load_verify_locations(cafile="ca.pem")
  # Example: Disable hostname check (generally not recommended)
  ssl_context.check_hostname = False
  # Example: Disable verification altogether (insecure, use with caution)
  ssl_context.verify_mode = ssl.CERT_NONE

  # Any kwargs passed to the factory should be a pass-through
  kwargs['verify'] = ssl_context
  return httpx.AsyncClient(**kwargs)

# Instantiate the root LLM Agent
root_agent = LlmAgent(
    model=LiteLlm(
        "vertex_ai/gemini-3.5-flash",
        vertex_location="global"
    ),
    name='looker_agent_email',
    description='Agent to answer questions about Looker data.',
    instruction='''
You are a helpful agent who can answer user questions about Looker data the user has access to.

Use the Looker tools to answer the question.
If you are unsure on what model to use, try defaulting to thelook
and if you are also unsure on the explore, try order_items if using thelook model.

If the user asks for it, summarize the results and draft an email for the user to review.
Upon the user's approval, go ahead and send the email to the user.
''',
    planner=BuiltInPlanner(
        thinking_config=ThinkingConfig(include_thoughts=False, thinking_budget=0)
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=MCP_SERVER_URL,
                httpx_client_factory=custom_httpx_client_factory
            ),
            header_provider=dynamic_header_provider,
            errlog=None,
            tool_filter=None,
        ),
        send_email
    ],
)

# Register the agent with the Vertex AI / ADK application entry point
app = AdkApp(agent=root_agent)

if __name__ == "__main__":
    # Local development & testing block
    # Note: To run this locally, ensure you set up proper Google credentials 
    # (e.g. gcloud auth application-default login) and inject the Gmail OAuth token 
    # into a state dict if you want to test email capabilities.
    print("Agent has been initialized and is ready for local testing/deployment!")
    # Example local run:
    # response = root_agent.run("List the order explores in thelook.")
    # print(response)
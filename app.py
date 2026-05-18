import os
from flask import Flask, request, jsonify
import requests
import re
from dotenv import load_dotenv
from supabase import create_client, Client
from twilio.rest import Client as TwilioClient
from flask_cors import CORS

load_dotenv()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)



app = Flask(__name__)
CORS(app)
# You will type this exact token into the Meta Dashboard later
VERIFY_TOKEN = "mntambo_triage_secret_123"
def send_automated_assignment(contractor_number, tenant_name, issue_description, apartment, unit):
    TwilioClient_sid = os.getenv("TWILIO_SID")
    TwilioClient_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_client = TwilioClient(TwilioClient_sid, TwilioClient_auth_token)
    message_body = (
            f"🛠️ *New Maintenance Job*\n\n"
            f"📍 *Location:* {apartment}, Unit {unit}\n"
            f"👤 *Tenant:* {tenant_name}\n"
            f"⚠️ *Issue:* {issue_description}\n\n"
            f"Reply *YES* to accept this job."
        )
    message= twilio_client.messages.create(
        from_="whatsapp:+14155238886",  # Twilio's sandbox number for WhatsApp
        body=message_body,
        to=f"whatsapp:{contractor_number}"
    )

def format_pronouns(text):
    """Flips first-person pronouns to second-person for professional bot replies."""
    
    # \b means "word boundary" so it only matches the exact word, not parts of other words.
    # The dictionary maps the exact word to its professional replacement.
    replacements = {
        r'\bi am\b': 'you are',
        r'\bi\'m\b': 'you are',
        r'\bmy\b': 'your',
        r'\bi\b': 'you',
        r'\bme\b': 'you',
        r'\bmine\b': 'yours'
        
    }
    
    formatted_text = text
    for pattern, replacement in replacements.items():
        # re.IGNORECASE makes sure it catches "My", "MY", and "my"
        formatted_text = re.sub(pattern, replacement, formatted_text, flags=re.IGNORECASE)
        
    return formatted_text
def send_whatsapp_reply(recipient_number, message_body):
    """
    Step 3: The WhatsApp Responder.
    This function will send a reply back to the tenant via the WhatsApp API.
    You will call this function from within receive_message() after processing the incoming message.
    """
    phone_id=os.getenv("WHATSAPP_PHONE_ID")
    target_url=f"https://graph.facebook.com/v25.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_BEARER_TOKEN')}",
        "Content-Type": "application/json"
                }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_number,  # Injecting the phone number here
        "type": "text",
        "text": {
            "preview_url": False, # Python uses Capital 'F' for False
            "body": message_body  # Injecting the actual message text here
        }
    }
    try:
        response = requests.post(target_url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"\n✅ outbound message status {response.status_code}:Meta Response {response.text}\n")
    except requests.RequestException as e:
        print(f"Error sending WhatsApp message: {e}")

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """
    Step 1: The Verification Endpoint.
    When you first link your server to Meta, they send a GET request here 
    with a challenge code. If your token matches, you send the code back.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode and token:
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("✅ WEBHOOK VERIFIED BY META")
            return challenge, 200
        else:
            return 'Verification token mismatch', 403
            
    return 'Server is running!', 200

@app.route('/api/v1/manager/add-property', methods=['POST'])
def add_property():
    data = request.json
    manager_id = data.get('manager_id')
    property_name = data.get('property_name')

    try:
        # 1. Look up the manager's paid limit
        manager_query = supabase.table('Managers').select('property_limit').eq('id', manager_id).execute()
        limit = manager_query.data[0]['property_limit']

        # 2. Count how many properties they currently have
        # (Assuming you have a 'Properties' table linked to their manager_id)
        count_query = supabase.table('Properties').select('id', count='exact').eq('manager_id', manager_id).execute()
        current_count = count_query.count

        # 3. THE BOUNCER: Check the limit
        if current_count >= limit:
            return jsonify({
                "status": "error", 
                "message": f"Upgrade required. You have reached your limit of {limit} properties."
            }), 403 # 403 means Forbidden

        # 4. If they pass the check, insert the property
        supabase.table('Properties').insert({
            "manager_id": manager_id,
            "name": property_name
        }).execute()

        return jsonify({"status": "success", "message": "Property added successfully!"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/v1/manager/<manager_id>/properties', methods=['GET'])
def get_manager_properties(manager_id):
    try:
        # 1. Get the limit
        manager_query = supabase.table('Managers').select('property_limit').eq('id', manager_id).execute()
        limit = manager_query.data[0]['property_limit'] if manager_query.data else 50

        # 2. Get the properties
        properties_query = supabase.table('Properties').select('*').eq('manager_id', manager_id).execute()
        properties = properties_query.data

        return jsonify({
            "status": "success", 
            "limit": limit,
            "current_count": len(properties),
            "properties": properties
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook', methods=['POST'])
def receive_message():
    """
    Step 2: The Message Catcher.
    Whenever a tenant sends a WhatsApp message, Meta sends a POST request here 
    containing the JSON payload you saw in the dashboard.
    """
    body = request.get_json()

    # Check if this is a WhatsApp API event
    if body.get('object'):
        try:
            # Drill down into Meta's nested JSON structure
            changes = body['entry'][0]['changes'][0]['value']
            
            # Check if it's an actual message (and not just a delivery receipt)
            if 'messages' in changes:
                from_number = changes['messages'][0]['from']
                msg_text = changes['messages'][0]['text']['body']
                
                print("\n" + "="*30)
                print("🚨 NEW MAINTENANCE TICKET 🚨")
                print(f"Phone Number: {from_number}")
                print(f"Message: {msg_text}")
                print("="*30 + "\n")
                
                clean_number= from_number.replace("+","").strip()
                print(f"searching for tenant with phone number: {clean_number}...")
                tenant_response= supabase.table("Tenants").select("*").eq("phone_number", clean_number).execute()
                if len(tenant_response.data)>0:
                    tenant= tenant_response.data[0]
                    tenant_id= tenant["id"]
                    tenant_name= tenant["name"]
                    print(f"✅ Tenant found: {tenant['name']} (ID: {tenant['id']})")
                    new_ticket= {
                        "tenant_id": tenant_id,
                        "issue_description": msg_text,
                        "status": "open"
                    }
                    supabase.table("Tickets").insert(new_ticket).execute()
                    print("✅ New ticket created in Supabase!")
                    professional_msg= format_pronouns(msg_text)
                    reply_text= f"Hi {tenant_name}, we've received your ticket regarding:{professional_msg} and your property manager has been notified"
                else:
                    print("⚠️ No tenant found with that phone number.")
                    reply_text= "Welcome to the maintenance portal! We don't recognize this number. Please contact your property manager to be added to the system."
                send_whatsapp_reply(from_number, reply_text)
                
        except KeyError:
            pass # Ignore status updates like "read" or "delivered" for now

        # You MUST return a 200 OK within seconds, or Meta will think your server crashed
        return jsonify({"status": "success"}), 200
        
    return jsonify({"status": "not a whatsapp event"}), 404
@app.route('/api/v1/dev/notify-contractor', methods=['POST'])
def trigger_assignment():
    """
    Step 4: The Dispatcher.
    Flutter hits this route when the manager clicks 'Assign Contractor'.
    """
    try:
        data = request.get_json()
        contractor_number = data.get('contractor_number')
        tenant_name = data.get('tenant_name')
        issue_description = data.get('issue_description')
        apartment = data.get('apartment', 'Unknown Building') # 👈 NEW
        unit = data.get('unit', 'Unknown Unit') # 👈 NEW

        send_automated_assignment(contractor_number, tenant_name, issue_description, apartment, unit)

        return jsonify({"status": "success", "message": "Contractor notified via Twilio"}), 200

    except Exception as e:
        print(f"Error in dispatcher: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/twilio/webhook', methods=['POST'])
def twilio_webhook():
    # 1. Get the incoming message and phone number
    incoming_msg = request.values.get('Body', '').lower().strip()
    from_number = request.values.get('From', '').replace('whatsapp:', '')

    print(f"📩 Incoming WhatsApp from {from_number}: {incoming_msg}")

    if incoming_msg == 'yes':
        try:
            # 2. Find the contractor in Supabase to make sure they exist
            contractor_query = supabase.table("Contractors").select("*").eq("phone_number", from_number).execute()
            
            if not contractor_query.data:
                return "<Response></Response>", 200 # Unknown number

            contractor_name = contractor_query.data[0]['name']

            # 3. Find the most recent 'open' ticket. 
            # (In a more advanced version, we'd link the ticket ID directly)
            ticket_query = supabase.table("Tickets").select("*").eq("status", "open").order("created_at", desc=True).limit(1).execute()

            if ticket_query.data:
                ticket_id = ticket_query.data[0]['id']
                
                # 4. Update the ticket status to 'In Progress'
                supabase.table("Tickets").update({"status": "In Progress"}).eq("id", ticket_id).execute()
                
                print(f"✅ Ticket {ticket_id} updated to 'In Progress' by {contractor_name}")

                # 5. Send confirmation back to contractor
                from twilio.twiml.messaging_response import MessagingResponse
                resp = MessagingResponse()
                resp.message(f"Thanks {contractor_name}! The job has been assigned to you. The tenant has been notified.")
                return str(resp)

        except Exception as e:
            print(f"❌ Error in Twilio webhook: {e}")

    return "<Response></Response>", 200
@app.route('/', methods=['GET'])
def health_check():
    return "✅ Triage Backend is Live and Running!", 200
@app.route('/api/v1/admin/create-manager', methods=['POST'])
def create_manager():
    data = request.json
    email = data.get('email')
    temp_password = data.get('temp_password')
    name = data.get('name')

    try:
        # 1. Create the user using the Admin API (Bypasses email verification)
        new_user = supabase.auth.admin.create_user({
            "email": email,
            "password": temp_password,
            "email_confirm": True 
        })

        # 2. Add them to your Managers table with the forced password flag
        # (Make sure you have a 'Managers' table in Supabase with these columns!)
        supabase.table('Managers').insert({
            "id": new_user.user.id,
            "name": name,
            "needs_password_change": True
        }).execute()

        return jsonify({"status": "success", "message": f"Manager {name} enrolled!"}), 200

    except Exception as e:
        print(f"Error creating manager: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/api/v1/admin/managers', methods=['GET'])
def get_managers():
    try:
        # Fetch all records from the Managers table
        response = supabase.table('Managers').select('*').execute()
        
        return jsonify({"status": "success", "data": response.data}), 200
    except Exception as e:
        print(f"Error fetching managers: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/api/v1/admin/toggle-access', methods=['POST'])
def toggle_access():
    data = request.get_json()
    manager_id = data.get('manager_id')
    new_status = data.get('is_active') # This will be True or False

    try:
        # Update the manager's status in your Supabase table
        response = supabase.table('Managers').update({'is_active': new_status}).eq('id', manager_id).execute()
        
        return jsonify({"success": True, "message": "Access updated successfully!"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
if __name__ == '__main__':
    # Runs the server locally on port 5000
    app.run(port=5000, debug=True) 
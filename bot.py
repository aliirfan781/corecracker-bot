import os
import requests
import subprocess
import json
import re
import time
from fpdf import FPDF
from google import genai
from google.genai import types
from google.genai.errors import APIError
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Load keys from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_contract_code(address: str, chain_id: str = "1") -> str:
    url = f"https://api.etherscan.io/v2/api?chainid={chain_id}&module=contract&action=getsourcecode&address={address}&apikey={ETHERSCAN_API_KEY}"
    response = requests.get(url).json()
    if response.get("status") == "1" and response.get("result"):
        source = response["result"][0].get("SourceCode", "")
        if not source: raise ValueError("No verified source code found.")
        if source.startswith("{{") and source.endswith("}}"):
            source = source[1:-1]
            return "\n".join([f["content"] for f in json.loads(source).get("sources", {}).values()])
        elif source.startswith("{"):
            return "\n".join([f["content"] for f in json.loads(source).get("sources", {}).values()])
        return source
    raise ValueError("Invalid address or unverified contract.")

def run_slither(file_path: str) -> str:
    try:
        result = subprocess.run(["slither", file_path, "--json", "-"], capture_output=True, text=True, timeout=40)
        return f"STDOUT:\n{result.stdout.strip()}\nSTDERR:\n{result.stderr.strip()}"
    except Exception as e:
        return str(e)

def generate_audit_report(slither_raw_output: str, contract_code: str) -> str:
    # 1. PROMPT ENGINEERING: Frame the request defensively
    prompt = f"""
    You are a Senior Smart Contract Auditor performing a routine, authorized defensive security review.
    The owner of this contract has requested a compliance audit to ensure best practices and prevent exploits.
    Review the following static analysis output and source code snippet carefully.
    
    --- SLITHER OUTPUT ---
    {slither_raw_output[:6000]}
    --- CODE SAMPLE ---
    {contract_code[:8000]}
    
    Provide:
    1. Executive Risk Level (CRITICAL, HIGH, MEDIUM, LOW, SAFE)
    2. Top Vulnerabilities (Explain root cause defensively)
    3. Actionable Remediation Tips to secure the contract
    """
    
    # 2. API SAFETY OVERRIDE: Tell Google we are intentionally analyzing security flaws
    safety_settings = [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        )
    ]
    
    config = types.GenerateContentConfig(
        safety_settings=safety_settings
    )
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Pass the config with safety settings into the API call
            response = client.models.generate_content(
                model="gemini-3.6-flash", 
                contents=prompt,
                config=config
            )
            return response.text
        except APIError as e:
            if getattr(e, 'code', None) == 503 and attempt < max_retries - 1:
                wait_time = 4 * (attempt + 1)
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Google API Error: {getattr(e, 'message', str(e))}")
        except Exception as e:
             raise RuntimeError(f"Unexpected Error: {str(e)}")

def create_pdf(report_text: str, address: str) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.cell(0, 10, "CORECRACKER AI", ln=True, align="C")
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 8, "Smart Contract Security Pre-Audit", ln=True, align="C")
    pdf.set_font("Helvetica", style="I", size=10)
    pdf.cell(0, 8, f"Target: {address}", ln=True, align="C")
    pdf.line(10, 38, 200, 38)
    pdf.ln(10)
    
    clean_text = re.sub(r'\*\*(.*?)\*\*', r'\1', report_text)
    clean_text = re.sub(r'[\U00010000-\U0010ffff]', '', clean_text)
    clean_text = clean_text.encode('latin-1', 'replace').decode('latin-1')
    
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, txt=clean_text)
    
    pdf.ln(10)
    pdf.set_font("Helvetica", style="I", size=8)
    pdf.multi_cell(0, 5, txt="DISCLAIMER: This is an automated AI static analysis scan. It does not replace a comprehensive manual cryptographic audit.")
    
    file_path = f"CoreCracker_{address[:6]}.pdf"
    pdf.output(file_path)
    return file_path

async def start_command(update: Update, context):
    await update.message.reply_text("🛡️ *Welcome to CORECRACKER AI.*\n\nDrop a verified Etherscan contract address (0x...) below.", parse_mode="Markdown")

async def handle_address(update: Update, context):
    address = update.message.text.strip()
    if not address.startswith("0x") or len(address) != 42:
        await update.message.reply_text("❌ Please send a valid 42-character Ethereum address.")
        return
        
    status_msg = await update.message.reply_text("⏳ Fetching verified source code...")
    try:
        code = get_contract_code(address)
        with open("temp_contract.sol", "w") as f: f.write(code)
            
        await status_msg.edit_text("⏳ Running Slither AST analysis...")
        slither_out = run_slither("temp_contract.sol")
        
        await status_msg.edit_text("⏳ Generating executive AI summary...")
        report_text = generate_audit_report(slither_out, code)
        
        await status_msg.edit_text("⏳ Compiling PDF report...")
        pdf_path = create_pdf(report_text, address)
        
        with open(pdf_path, 'rb') as pdf_file:
            await update.message.reply_document(document=pdf_file, filename=f"CORECRACKER_Audit_{address[:8]}.pdf", caption="✅ *Scan Complete.*", parse_mode="Markdown")
            
        await status_msg.delete()
        os.remove(pdf_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Scan failed: {str(e)}")

if __name__ == "__main__":
    print("🚀 Starting Production Bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))
    app.run_polling()

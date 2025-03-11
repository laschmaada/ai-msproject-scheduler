from flask import Flask, render_template, request, jsonify, send_file
import openai  # For OpenAI API
import os
import google.generativeai as genai  # For Gemini API
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import uuid
import io
import markdown
import PyPDF2
import tempfile
import shutil

app = Flask(__name__)

# Configure API Keys (Use Environment Variables in Production)
openai.api_key = os.environ.get("OPENAI_API_KEY", "")
gemini_api_key = os.environ.get("GEMINI_API_KEY", "")

# Store the latest generated schedule
latest_schedule = None

# Configure Gemini if API key is available
if gemini_api_key:
    genai.configure(api_key=gemini_api_key)

# Store the latest generated schedule
latest_schedule = None

# Create a temporary directory for uploaded PDFs
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'ai_scheduler_pdfs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Store uploaded PDF information
pdf_storage = {}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload_pdf", methods=["POST"])
def upload_pdf():
    try:
        if 'pdf' not in request.files:
            return jsonify({"success": False, "error": "No file part"}), 400
            
        file = request.files['pdf']
        
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected"}), 400
            
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({"success": False, "error": "Only PDF files are allowed"}), 400
        
        # Generate a unique ID for this PDF
        pdf_id = str(uuid.uuid4())
        
        # Save the file to the temporary directory
        pdf_path = os.path.join(UPLOAD_FOLDER, f"{pdf_id}.pdf")
        file.save(pdf_path)
        
        # Extract text from the PDF
        pdf_text = extract_text_from_pdf(pdf_path)
        
        # Store the PDF information
        pdf_storage[pdf_id] = {
            "filename": file.filename,
            "path": pdf_path,
            "text": pdf_text
        }
        
        return jsonify({"success": True, "pdf_id": pdf_id})
        
    except Exception as e:
        print(f"PDF upload error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page_num in range(len(reader.pages)):
                text += reader.pages[page_num].extract_text() + "\n\n"
    except Exception as e:
        print(f"Error extracting text from PDF: {str(e)}")
    return text

@app.route("/generate", methods=["POST"])
def generate_schedule():
    global latest_schedule
    try:
        user_input = request.json.get("text")
        model_choice = request.json.get("model", "openai")  # Default to OpenAI if not specified
        pdf_id = request.json.get("pdf_id")
        
        if not user_input:
            return jsonify({"error": "No input provided"}), 400
        
        system_prompt = """You are an AI that generates structured project schedules for MS Project. 
        Format your response with clear task names, durations, dependencies, and resources.
        
        Use the following format for each task:
        
        ## Task 1: [Task Name]
        - Duration: X days
        - Start: YYYY-MM-DD
        - Finish: YYYY-MM-DD
        - Predecessors: Task ID(s) or None
        - Resources: Person name or role
        - Notes: Additional details
        
        ## Task 2: [Task Name]
        - Duration: X days
        - Start: YYYY-MM-DD
        - Finish: YYYY-MM-DD
        - Predecessors: 1
        - Resources: Person name or role
        - Notes: Additional details
        
        Start with a project summary and organize tasks hierarchically.
        Include milestones with zero duration.
        Use numeric IDs for predecessors (e.g., 1, 2, 3).
        Separate multiple resources with commas.
        """
        
        # Add PDF context if available
        pdf_context = ""
        if pdf_id and pdf_id in pdf_storage:
            pdf_text = pdf_storage[pdf_id]["text"]
            pdf_context = f"\n\nHere is additional context from the uploaded document:\n\n{pdf_text}\n\nUse the information from this document to inform your project schedule generation."
        
        # Combine user input with PDF context
        full_input = user_input
        if pdf_context:
            full_input += "\n\nPlease use the following document as additional context: " + pdf_context
        
        if model_choice == "gemini" and gemini_api_key:
            # Use Gemini API
            gemini_model = genai.GenerativeModel('gemini-1.5-pro')
            response = gemini_model.generate_content([
                {"role": "user", "parts": [system_prompt + "\n\n" + full_input]}
            ])
            schedule_text = response.text
        else:
            # Use OpenAI API
            if openai.api_key == "your_openai_api_key_here":
                return jsonify({"error": "OpenAI API key not configured. Please set your API key."}), 500
                
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_input}
                ]
            )
            schedule_text = response.choices[0].message.content
        
        # Store the latest schedule for export
        latest_schedule = schedule_text
        
        # Convert markdown to HTML for display
        html_content = markdown.markdown(schedule_text)
            
        return jsonify({"schedule": html_content})
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": f"Error generating schedule: {str(e)}"}), 500

@app.route("/export", methods=["GET"])
def export_schedule():
    global latest_schedule
    
    if not latest_schedule:
        return jsonify({"error": "No schedule has been generated yet"}), 400
    
    try:
        # Parse the markdown schedule into MS Project XML format
        xml_content = convert_to_ms_project_xml(latest_schedule)
        
        # Create a file-like object in memory
        buffer = io.BytesIO(xml_content.encode('utf-8'))
        buffer.seek(0)
        
        # Send the file to the client
        return send_file(
            buffer,
            mimetype='application/xml',
            as_attachment=True,
            download_name='project_schedule.xml'
        )
    
    except Exception as e:
        print(f"Export error: {str(e)}")
        return jsonify({"error": f"Error exporting schedule: {str(e)}"}), 500

def convert_to_ms_project_xml(markdown_text):
    # Create XML with proper MS Project format
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    xml += '<Project xmlns="http://schemas.microsoft.com/project">\n'
    
    # Add project header information
    current_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    xml += f'  <SaveVersion>14</SaveVersion>\n'
    xml += f'  <Title>AI Generated Project Schedule</Title>\n'
    xml += f'  <ScheduleFromStart>1</ScheduleFromStart>\n'
    xml += f'  <StartDate>{current_date}</StartDate>\n'
    xml += f'  <FinishDate>{(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")}</FinishDate>\n'
    xml += f'  <FYStartDate>1</FYStartDate>\n'
    xml += f'  <CriticalSlackLimit>0</CriticalSlackLimit>\n'
    xml += f'  <CurrencyDigits>2</CurrencyDigits>\n'
    xml += f'  <CurrencySymbol>$</CurrencySymbol>\n'
    xml += f'  <CalendarUID>1</CalendarUID>\n'
    xml += f'  <DefaultStartTime>08:00:00</DefaultStartTime>\n'
    xml += f'  <DefaultFinishTime>17:00:00</DefaultFinishTime>\n'
    xml += f'  <MinutesPerDay>480</MinutesPerDay>\n'
    xml += f'  <MinutesPerWeek>2400</MinutesPerWeek>\n'
    xml += f'  <DaysPerMonth>20</DaysPerMonth>\n'
    
    # Helper function to escape XML special characters
    def escape_xml(text):
        if text is None:
            return ""
        return (text.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
                   .replace("\"", "&quot;")
                   .replace("'", "&apos;"))
    
    # Parse tasks from markdown
    tasks = []
    resources = set()
    
    task_pattern = r"##\s+(.*?)\n([\s\S]*?)(?=##|$)"
    task_matches = re.finditer(task_pattern, markdown_text)
    
    task_id = 1
    start_date = datetime.now()
    
    for match in task_matches:
        task_name = escape_xml(match.group(1).strip())
        task_details = match.group(2).strip()
        
        # Extract task information
        duration_match = re.search(r"Duration:\s*(.*?)(?:\n|$)", task_details)
        duration_text = duration_match.group(1) if duration_match else "1 day"
        duration_days = int(re.search(r"(\d+)", duration_text).group(1)) if re.search(r"(\d+)", duration_text) else 1
        
        # Extract start date if available
        start_match = re.search(r"Start:\s*(.*?)(?:\n|$)", task_details)
        if start_match and re.match(r"\d{4}-\d{2}-\d{2}", start_match.group(1)):
            task_start = datetime.strptime(start_match.group(1), "%Y-%m-%d")
        else:
            task_start = start_date
        
        # Calculate finish date
        finish_date = task_start + timedelta(days=duration_days)
        
        # Extract predecessors
        pred_match = re.search(r"Predecessors:\s*(.*?)(?:\n|$)", task_details)
        predecessors = []
        if pred_match and pred_match.group(1).lower() != "none":
            pred_ids = re.findall(r'\d+', pred_match.group(1))
            predecessors = [int(pid) for pid in pred_ids if pid.isdigit()]
        
        # Extract resources
        resource_match = re.search(r"Resources:\s*(.*?)(?:\n|$)", task_details)
        resource_names = []
        if resource_match:
            resource_text = resource_match.group(1)
            resource_names = [r.strip() for r in resource_text.split(',')]
            for r in resource_names:
                resources.add(r)
        
        # Extract notes
        notes_match = re.search(r"Notes:\s*(.*?)(?:\n|$)", task_details)
        notes = escape_xml(notes_match.group(1) if notes_match else "")
        
        # Add task to list
        tasks.append({
            'id': task_id,
            'name': task_name,
            'duration': duration_days,
            'start': task_start,
            'finish': finish_date,
            'predecessors': predecessors,
            'resources': resource_names,
            'notes': notes,
            'is_milestone': 'milestone' in task_name.lower() or duration_days == 0
        })
        
        task_id += 1
        start_date = finish_date  # For the next task if no specific date
    
    # Add tasks to XML
    xml += '  <Tasks>\n'
    
    for task in tasks:
        xml += f'    <Task>\n'
        xml += f'      <UID>{task["id"]}</UID>\n'
        xml += f'      <ID>{task["id"]}</ID>\n'
        xml += f'      <Name>{task["name"]}</Name>\n'
        
        if task["is_milestone"]:
            xml += f'      <Milestone>1</Milestone>\n'
            xml += f'      <Duration>PT0H0M0S</Duration>\n'
        else:
            xml += f'      <Milestone>0</Milestone>\n'
            xml += f'      <Duration>PT{8 * task["duration"]}H0M0S</Duration>\n'
        
        xml += f'      <Start>{task["start"].strftime("%Y-%m-%dT%H:%M:%S")}</Start>\n'
        xml += f'      <Finish>{task["finish"].strftime("%Y-%m-%dT%H:%M:%S")}</Finish>\n'
        
        if task["notes"]:
            xml += f'      <Notes>{task["notes"]}</Notes>\n'
        
        # Add predecessors
        for pred_id in task["predecessors"]:
            xml += f'      <PredecessorLink>\n'
            xml += f'        <PredecessorUID>{pred_id}</PredecessorUID>\n'
            xml += f'        <Type>1</Type>\n'  # Finish-to-Start relationship
            xml += f'      </PredecessorLink>\n'
        
        xml += f'    </Task>\n'
    
    xml += '  </Tasks>\n'
    
    # Add resources to XML
    xml += '  <Resources>\n'
    resource_id = 1
    resource_map = {}
    
    for resource_name in resources:
        escaped_name = escape_xml(resource_name)
        resource_map[resource_name] = resource_id
        xml += f'    <Resource>\n'
        xml += f'      <UID>{resource_id}</UID>\n'
        xml += f'      <ID>{resource_id}</ID>\n'
        xml += f'      <Name>{escaped_name}</Name>\n'
        xml += f'      <Type>1</Type>\n'  # Work resource type
        xml += f'    </Resource>\n'
        resource_id += 1
    
    xml += '  </Resources>\n'
    
    # Add assignments to XML
    xml += '  <Assignments>\n'
    assignment_id = 1
    
    for task in tasks:
        for resource_name in task["resources"]:
            if resource_name in resource_map:
                xml += f'    <Assignment>\n'
                xml += f'      <UID>{assignment_id}</UID>\n'
                xml += f'      <TaskUID>{task["id"]}</TaskUID>\n'
                xml += f'      <ResourceUID>{resource_map[resource_name]}</ResourceUID>\n'
                xml += f'      <Units>1</Units>\n'  # 100% allocation
                xml += f'    </Assignment>\n'
                assignment_id += 1
    
    xml += '  </Assignments>\n'
    xml += '</Project>'
    
    return xml

if __name__ == "__main__":
    app.run(debug=True)

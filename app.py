import os
import uuid
import boto3
import httpx
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# ─────────────────────────────────────────
# Factor 11: Logs — write to stdout only
# ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

from flask_cors import CORS
CORS(app)

# ─────────────────────────────────────────
# Factor 3: Config — read from environment variables
# Never hardcode these values!
# ─────────────────────────────────────────
S3_BUCKET_NAME     = os.environ.get("S3_BUCKET_NAME")
AI_SERVICE_URL     = os.environ.get("AI_SERVICE_URL")
DYNAMODB_TABLE     = os.environ.get("DYNAMODB_TABLE")
AWS_REGION         = os.environ.get("AWS_REGION", "us-east-1")
PORT               = int(os.environ.get("PORT", 8080))
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx"}

# ─────────────────────────────────────────
# Factor 4: Backing Services — S3 and DynamoDB
# connected via config, not hardcoded
# ─────────────────────────────────────────
s3_client       = boto3.client("s3", region_name=AWS_REGION)
dynamodb        = boto3.resource("dynamodb", region_name=AWS_REGION)
results_table   = dynamodb.Table(DYNAMODB_TABLE) if DYNAMODB_TABLE else None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────
# Factor 14: Telemetry — health check endpoint
# ─────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "upload-service"}), 200


# ─────────────────────────────────────────
# Factor 13: API First — well defined REST endpoints
# POST /upload — upload a CV file
# ─────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_cv():
    # 1. Validate file exists in request
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF, DOC, DOCX files are allowed"}), 400

    # 2. Generate a unique ID for this CV
    cv_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    s3_key = f"cvs/{cv_id}/{filename}"

    logger.info(f"Uploading CV with id={cv_id} to S3")

    # 3. Upload file to S3
    # Factor 6: Processes — stateless, file goes to S3 not local disk
    try:
        s3_client.upload_fileobj(
            file,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={"ContentType": file.content_type}
        )
        logger.info(f"CV uploaded to S3: {s3_key}")
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        return jsonify({"error": "Failed to upload file"}), 500

    # 4. Save initial record to DynamoDB (status: pending)
    try:
        results_table.put_item(Item={
            "cv_id":    cv_id,
            "filename": filename,
            "s3_key":   s3_key,
            "status":   "pending"
        })
        logger.info(f"Saved pending record to DynamoDB for cv_id={cv_id}")
    except Exception as e:
        logger.error(f"DynamoDB write failed: {e}")
        return jsonify({"error": "Failed to save record"}), 500

    # 5. Trigger the AI Analysis Service asynchronously
    try:
        response = httpx.post(
            f"{AI_SERVICE_URL}/analyze",
            json={"cv_id": cv_id, "s3_key": s3_key},
            timeout=5.0
        )
        logger.info(f"AI service triggered for cv_id={cv_id}, status={response.status_code}")
    except Exception as e:
        # Non-blocking — CV is uploaded even if AI trigger fails
        logger.warning(f"Could not trigger AI service: {e}")

    # 6. Return the cv_id so the user can poll for results
    return jsonify({
        "message": "CV uploaded successfully",
        "cv_id":   cv_id,
        "status":  "pending"
    }), 202


# ─────────────────────────────────────────
# GET /results/<cv_id> — get analysis results
# ─────────────────────────────────────────
@app.route("/results/<cv_id>", methods=["GET"])
def get_results(cv_id):
    logger.info(f"Fetching results for cv_id={cv_id}")

    try:
        response = results_table.get_item(Key={"cv_id": cv_id})
        item = response.get("Item")

        if not item:
            return jsonify({"error": "CV not found"}), 404

        return jsonify(item), 200

    except Exception as e:
        logger.error(f"DynamoDB read failed: {e}")
        return jsonify({"error": "Failed to fetch results"}), 500


# ─────────────────────────────────────────
# Factor 7: Port Binding — service binds to PORT
# Factor 9: Disposability — Flask handles graceful shutdown
# ─────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Upload Service starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)

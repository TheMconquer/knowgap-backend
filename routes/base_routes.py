from config import Config
from quart import jsonify, request, Response

def init_base_routes(app):
    @app.route('/')
    async def hello_world():
        return jsonify('Welcome to the KnowGap Backend API!')

    @app.before_request
    async def handle_options_request():
        if request.method == 'OPTIONS':
            response = Response()
            origin = request.headers.get('Origin')
            allowed_origins = [
                "https://canvas.instructure.com",
                "https://webcourses.ucf.edu",
                Config.FRONTEND_URL,
                Config.FRONTEND_HOSTING_URL,
                "http://localhost:3000"
            ]
            if origin in allowed_origins or (origin and origin.startswith('chrome-extension://')):
                response.headers['Access-Control-Allow-Origin'] = origin
            
            response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Origin, X-Requested-With')
            response.headers.add('Access-Control-Allow-Credentials', 'true')
            return response

#!/usr/bin/env python3
"""
🎵 Music24 Professional API Entry Point 🎵
This file serves as the main entry point for deployment platforms like Render.
"""

# Import the professional backend
from trending_pro import app, config, logger

if __name__ == '__main__':
    logger.info(f"🎵 Starting Music24 Professional API v4.0.0")
    logger.info(f"🌍 Environment: {config.ENVIRONMENT}")
    logger.info(f"🔧 Debug mode: {config.DEBUG}")
    logger.info(f"🔒 API key required: {config.API_KEY_REQUIRED}")
    logger.info(f"🧠 ML recommendations: {config.ENABLE_ML_RECOMMENDATIONS}")
    logger.info(f"📊 Metrics enabled: {config.ENABLE_METRICS}")
    
    # Production-ready server configuration
    if config.ENVIRONMENT == 'production':
        # Use Gunicorn in production
        logger.info("🚀 Starting production server")
        logger.info(f"📡 Server will be available at http://{config.HOST}:{config.PORT}")
        app.run(
            host=config.HOST,
            port=config.PORT,
            debug=False,
            threaded=True
        )
    else:
        # Development server
        logger.info("🔧 Starting development server")
        app.run(
            host=config.HOST,
            port=config.PORT,
            debug=config.DEBUG,
            threaded=True
        )

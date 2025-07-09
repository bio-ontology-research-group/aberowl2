import src.RequestManager

response.setContentType("application/json")
out << RequestManager.forwardToQueryParser("/test_pipeline", null, "POST")

import src.RequestManager

def body = request.getReader().getText()
response.setContentType("application/json")
out << RequestManager.forwardToQueryParser("/parse_v2", body, "POST")

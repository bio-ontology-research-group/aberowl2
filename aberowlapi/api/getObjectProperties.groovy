import groovy.json.JsonBuilder
import org.json.simple.JSONValue;
import java.net.URLDecoder;
import src.util.Util

if(!application) {
    application = request.getApplication(true);
}

def params = Util.extractParams(request);
def property = params.property;
def manager = application.manager;

response.contentType = 'application/json';

if(manager) {
    if (property == null) {
        def objectProperties = manager.getObjectProperties()
        print(new JsonBuilder(objectProperties))
    } else {
        property = URLDecoder.decode(property, "UTF-8")
        def objectProperties = manager.getObjectProperties(property)
        print(new JsonBuilder(objectProperties))
    }
} else {
    print('{status: "error", message: "Please provide an ontology!"}')
}

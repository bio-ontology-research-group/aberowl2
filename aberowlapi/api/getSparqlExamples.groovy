import groovy.json.JsonOutput

def result = [
    exampleSuperclassLabel: requestManager.exampleSuperclassLabel,
    exampleSubclassExpression: requestManager.exampleSubclassExpression
]

println(JsonOutput.toJson(result))

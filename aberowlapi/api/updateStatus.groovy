/**
 * updateStatus.groovy
 *
 * Poll the status of an async update or indexing task started by
 * updateOntology.groovy or triggerIndexing.groovy.
 *
 * GET/POST parameters:
 *   taskId - the task ID returned by the triggering endpoint
 */

import groovy.json.*
import src.util.Util

def params = Util.extractParams(request)
def taskId = params.taskId

response.contentType = 'application/json'

if (!taskId) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'taskId parameter required'])
    return
}

def updateTasks = application.getAttribute("updateTasks")
if (updateTasks == null || !updateTasks.containsKey(taskId)) {
    response.setStatus(404)
    println new JsonBuilder([status: 'error', message: "Task not found: ${taskId}"])
    return
}

println new JsonBuilder(updateTasks[taskId])

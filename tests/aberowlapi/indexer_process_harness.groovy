import src.IndexerProcess
import groovy.json.JsonSlurper
import java.util.concurrent.TimeUnit

int checks = 0
def check = { boolean value -> assert value; checks++ }
// No override must preserve the environment exactly, including an absent value.
for (String extra : [null, '', '  \t\n']) {
    check(IndexerProcess.javaOptions('-Xmx1g -Dshared=kept', extra) == null)
    check(IndexerProcess.javaOptions(null, extra) == null)
}
check(IndexerProcess.javaOptions(null, ' -Xmx128m ') == '-Xmx128m')
check(IndexerProcess.javaOptions('', '-Xmx128m') == '-Xmx128m')
check(IndexerProcess.javaOptions('-Xmx1g -Dshared=kept', ' -Xmx128m -Xms64m ') ==
      '-Xmx1g -Dshared=kept -Xmx128m -Xms64m')

// Exercise the launcher's actual flag precedence and property inheritance.
def parentMax = Runtime.runtime.maxMemory()
def inherited = (System.getenv('JAVA_OPTS') ?: '') +
    ' -Dindexer.shared=kept -Dgrape.root=/tmp/indexer-test-grapes -DHPPC_ITERATOR_POOLSIZE=4'
def probe = '''import groovy.json.JsonOutput
def diagnostic=java.lang.management.ManagementFactory.getPlatformMXBean(com.sun.management.HotSpotDiagnosticMXBean)
println JsonOutput.toJson([maxHeap:diagnostic.getVMOption('MaxHeapSize').value.toLong(),
    runtimeMax:Runtime.runtime.maxMemory(), grapeRoot:System.getProperty('grape.root'),
    poolSize:System.getProperty('HPPC_ITERATOR_POOLSIZE'),
    shared:System.getProperty('indexer.shared'), child:System.getProperty('indexer.child'),
    flags:java.lang.management.ManagementFactory.runtimeMXBean.inputArguments])'''
def launch = { String extra ->
    def builder = new ProcessBuilder('groovy', '-e', probe).redirectErrorStream(true)
    builder.environment().put('JAVA_OPTS', inherited)
    def options = IndexerProcess.javaOptions(builder.environment().get('JAVA_OPTS'), extra)
    if (options != null) builder.environment().put('JAVA_OPTS', options)
    def result = IndexerProcess.collect(builder.start(), '')
    check(result.exitCode == 0)
    new JsonSlurper().parseText(result.outputTail.readLines().last())
}
def baseline = launch(null)
def blank = launch(' \t')
def limited = launch('-Xmx96m -Xms16m -Dindexer.child=enabled')
check(blank.flags == baseline.flags)
check(blank.maxHeap == baseline.maxHeap)
check(limited.maxHeap == 96L * 1024 * 1024)
check(limited.runtimeMax <= limited.maxHeap && limited.runtimeMax >= limited.maxHeap * 0.9)
check(limited.shared == 'kept' && limited.child == 'enabled')
check(limited.grapeRoot == '/tmp/indexer-test-grapes' && limited.poolSize == '4')
check(baseline.shared == 'kept' && baseline.child == null)
check(Runtime.runtime.maxMemory() == parentMax)
check(System.getProperty('indexer.child') == null)


// One line much larger than the test JVM heap: neither readline nor .text is safe.
def noisy = new ProcessBuilder('sh', '-c', 'cat >/dev/null; head -c 134217728 /dev/zero; printf END-OF-OUTPUT').redirectErrorStream(true).start()
def collected = IndexerProcess.collect(noisy, '{}')
check(collected.exitCode == 0)
check(collected.outputTail.size() == IndexerProcess.TAIL_CHARS)
check(collected.outputTail.endsWith('END-OF-OUTPUT'))
check(!noisy.isAlive())

def bad = new ProcessBuilder('sh', '-c', 'cat >/dev/null; printf diagnostic >&2; exit 7').redirectErrorStream(true).start()
def failed = IndexerProcess.collect(bad, '{}')
check(failed.exitCode == 7)
check(failed.outputTail == 'diagnostic')
check(!bad.isAlive())

class BrokenReaderProcess extends Process {
    final Process child
    BrokenReaderProcess(Process child) { this.child = child }
    OutputStream getOutputStream() { child.outputStream }
    InputStream getInputStream() { new InputStream() { int read() { throw new IOException('reader failed') } } }
    InputStream getErrorStream() { child.errorStream }
    int waitFor() { child.waitFor() }
    boolean waitFor(long timeout, TimeUnit unit) { child.waitFor(timeout, unit) }
    int exitValue() { child.exitValue() }
    void destroy() { child.destroy() }
    Process destroyForcibly() { child.destroyForcibly(); this }
    boolean isAlive() { child.isAlive() }
}
def child = new ProcessBuilder('sh', '-c', 'cat >/dev/null; exec sleep 60').start()
try {
    IndexerProcess.collect(new BrokenReaderProcess(child), '{}')
    assert false: 'reader failure was swallowed'
} catch (IOException expected) {
    check(expected.message == 'reader failed')
    check(!child.isAlive())
}
println "Indexer process checks passed: ${checks}"

class BrokenWriterProcess extends BrokenReaderProcess {
    BrokenWriterProcess(Process child) { super(child) }
    InputStream getInputStream() { child.inputStream }
    OutputStream getOutputStream() { new OutputStream() { void write(int value) { throw new IOException('writer failed') } } }
}
def writerChild = new ProcessBuilder('sh', '-c', 'exec sleep 60').start()
try {
    IndexerProcess.collect(new BrokenWriterProcess(writerChild), '{}')
    assert false: 'writer failure was swallowed'
} catch (IOException expected) {
    check(expected.message == 'writer failed')
    check(!writerChild.isAlive())
}

class InterruptedProcess extends BrokenReaderProcess {
    boolean firstWait = true
    InterruptedProcess(Process child) { super(child) }
    InputStream getInputStream() { new ByteArrayInputStream(new byte[0]) }
    int waitFor() {
        if (firstWait) { firstWait = false; throw new InterruptedException('interrupted wait') }
        return child.waitFor()
    }
}
def interruptedChild = new ProcessBuilder('sh', '-c', 'exec sleep 60').start()
try {
    IndexerProcess.collect(new InterruptedProcess(interruptedChild), '{}')
    assert false: 'interruption was swallowed'
} catch (InterruptedException expected) {
    check(!interruptedChild.isAlive())
    check(Thread.currentThread().isInterrupted())
} finally { Thread.interrupted() }
def unicode = ('αβγ-' * 3000) + '€-final'
check(IndexerProcess.readTail(new ByteArrayInputStream(unicode.getBytes('UTF-8'))) == unicode.takeRight(IndexerProcess.TAIL_CHARS))
println "All indexer process checks passed: ${checks}"

import src.IndexerProcess
import java.util.concurrent.TimeUnit

int checks = 0
def check = { boolean value -> assert value; checks++ }
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

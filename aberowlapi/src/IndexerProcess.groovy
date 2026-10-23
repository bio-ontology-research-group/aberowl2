package src

import java.util.concurrent.TimeUnit

/** Drain indexer diagnostics without retaining its complete output in the worker heap. */
class IndexerProcess {
    static final int TAIL_CHARS = 4096

    static Map collect(Process process, String metadata) {
        try {
            process.outputStream.withWriter('UTF-8') { writer ->
                writer.write(metadata)
                writer.flush()
            }
            String tail = readTail(process.inputStream)
            int exitCode = process.waitFor()
            return [exitCode: exitCode, outputTail: tail]
        } catch (Throwable failure) {
            // A failed reader/writer must not leave a child writing an index after
            // its task reports failure or its validation export is deleted.
            stopAndWait(process)
            if (failure instanceof InterruptedException) Thread.currentThread().interrupt()
            throw failure
        } finally {
            for (Closeable stream : [process.inputStream, process.outputStream, process.errorStream]) {
                try { stream.close() } catch (Exception ignored) { }
            }
        }
    }

    static String readTail(InputStream stream) {
        StringBuilder tail = new StringBuilder(TAIL_CHARS)
        char[] buffer = new char[8192]
        stream.withReader('UTF-8') { reader ->
            int count
            while ((count = reader.read(buffer)) != -1) {
                tail.append(buffer, 0, count)
                if (tail.length() > TAIL_CHARS) tail.delete(0, tail.length() - TAIL_CHARS)
            }
        }
        return tail.toString()
    }

    private static void stopAndWait(Process process) {
        boolean interrupted = Thread.interrupted()
        try {
            process.destroy()
            try {
                if (!process.waitFor(10, TimeUnit.SECONDS)) process.destroyForcibly()
            } catch (InterruptedException ignored) {
                interrupted = true
                process.destroyForcibly()
            }
            // Keep the task pending until the exact child has actually exited.
            while (process.isAlive()) {
                try { process.waitFor() }
                catch (InterruptedException ignored) {
                    interrupted = true
                    process.destroyForcibly()
                }
            }
        } finally {
            if (interrupted) Thread.currentThread().interrupt()
        }
    }
}

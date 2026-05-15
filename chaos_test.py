import threading
import time
import traceback
import sys
from adapters.engine import Engine
from self_check import make_scenario

def test_concurrency():
    try:
        e = Engine()
        train, signals = make_scenario(seed=42)
        # Give it some initial data
        e.ingest(train[:20])
        
        success_count = 0
        exceptions = []

        def worker(i):
            nonlocal success_count
            try:
                # Re-ingest an event (to test concurrent writes)
                e.ingest([train[i % len(train)]])
                # Reconstruct context (to test concurrent reads/writes)
                ctx = e.reconstruct_context(signals[0], mode="fast")
                if ctx:
                    success_count += 1
            except Exception as ex:
                exceptions.append(traceback.format_exc())

        threads = []
        for i in range(100):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)

        t0 = time.time()
        for t in threads: t.start()
        for t in threads: t.join()
        t1 = time.time()

        print(f"Concurrency test: {success_count}/100 succeeded in {t1-t0:.2f}s")
        if exceptions:
            print(f"Caught {len(exceptions)} exceptions!")
            print(f"First exception:\n{exceptions[0]}")
            sys.exit(1)
        else:
            print("No race conditions detected.")
    except Exception as e:
        print("Main thread error:")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    test_concurrency()

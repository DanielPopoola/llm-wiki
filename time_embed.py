import time

t0 = time.time()
from sentence_transformers import SentenceTransformer

t1 = time.time()
print(f"import time: {t1 - t0:.2f}s")

model = SentenceTransformer("BAAI/bge-small-en-v1.5")
t2 = time.time()
print(f"model load time: {t2 - t1:.2f}s")

vec = model.encode("test query")
t3 = time.time()
print(f"encode time: {t3 - t2:.2f}s")

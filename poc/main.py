from openai import OpenAI
from dotenv import load_dotenv
import os
from pypdf import PdfReader
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()
rit_api_key = os.getenv("RIT_API_KEY")
if not rit_api_key:
  print("No RIT_API_KEY found")
  exit()

client = OpenAI(
  base_url="https://api.genai.gccis.rit.edu/v1",
  api_key=rit_api_key
)

# RAG
chroma_client = chromadb.Client()
collection = chroma_client.create_collection(name="rag_knowledge_base")

def extract_text_from_pdf(pdf_path: str) -> str:
  """Reads all pages of a PDF file and joins them into a single string."""
  reader = PdfReader(pdf_path)
  full_text = []
  
  for page in reader.pages:
    text = page.extract_text()
    if text:  # Ensure the page isn't an empty image
      full_text.append(text)
          
  return "\n".join(full_text)

raw_document_text = extract_text_from_pdf("data/pdfs/Andrew_Xie_Resume.pdf")

text_splitter = RecursiveCharacterTextSplitter(chunk_size=150, chunk_overlap=20)
chunks = text_splitter.split_text(raw_document_text)

collection.add(
  documents=chunks,
  ids=[f"chunk_{i}" for i in range(len(chunks))]
)

def retrieve_context(query: str, top_k: int = 2) -> str:
  """Queries the local vector store and joins relevant document chunks."""
  results = collection.query(
    query_texts=[query],
    n_results=top_k
  )
  flattened_documents = [doc for sublist in results['documents'] for doc in sublist] # type: ignore
  return "\n---\n".join(flattened_documents)

user_query = "where did andrew work?"
context = retrieve_context(user_query, top_k=2)

system_prompt = f"""You are a helpful assistant. Use the following context to accurately answer the user's question. 
If the answer cannot be found in the context, say "I don't have enough information."

Context:
{context}"""



response = client.chat.completions.create(
  model="qwen3:latest",
  messages=[
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_query}
  ],
  temperature=0.0
)

# Print the final result grounded by your data
print(response.choices[0].message.content)
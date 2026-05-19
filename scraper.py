import requests
from bs4 import BeautifulSoup
import pandas as pd

def scrape_books():
    # Target URL
    base_url = "https://books.toscrape.com/catalogue/page-{}.html"
    all_books = []
    
    print("開始爬取書籍數據...")
    
    # Loop through the first 3 pages
    for page in range(1, 4):
        url = base_url.format(page)
        print(f"正在爬取第 {page} 頁: {url}")
        
        # 1. Send GET request to the page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Check if the request was successful
        
        # 2. Parse the HTML content
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 3. Extract book data
        books = soup.find_all('article', class_="product_pod")
        
        for book in books:
            # Extract title
            title = book.find("h3").find("a")["title"]
            # Extract price
            price = book.find("p", class_="price_color").text.strip()
            # Extract rating
            rating_class = book.find("p", class_="star-rating")["class"][1]
            rating_map = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
            rating = rating_map.get(rating_class, 0)
            # Extract availability
            availability = book.find("p", class_="instock availability").text.strip()
            
            all_books.append({
                "書名": title,
                "價格": price,
                "評分": rating,
                "庫存狀態": availability
            })
            
    print(f"爬取完成！共獲取到{len(all_books)}本書籍數據")
    return all_books

def save_to_excel(data, filename="books_data.xlsx"):
    # Save the data to an Excel file
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False)
    print(f"數據已保存到 {filename}")
    
if __name__ == "__main__":
    try:
        books_data = scrape_books()
        save_to_excel(books_data)
        
        # Print the first 5 entries as a sample
        print("\n前5本書籍數據預覽:")
        for i, book in enumerate(books_data[:5], start=1):
            print(f"{i}. {book['書名']} - {book['價格']} - 評分{book['評分']}星, 庫存狀態: {book['庫存狀態']}")
            
    except Exception as e:
        print(f"爬取過程中出現錯誤: {e}")
        
        
                              
                              
        
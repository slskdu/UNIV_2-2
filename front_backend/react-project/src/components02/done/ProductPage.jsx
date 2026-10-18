// 1~2. 부모 컴포넌트에서 자식 컴포넌트로 데이터(Props) 전달 및 Fragment 사용 목적을 설명합니다.
import React from 'react'
import Product from './Product' // 5. 자식 컴포넌트인 'Product'를 불러옵니다.
import '../Style02.css'

// 8~12. 화면에 출력할 가짜 데이터(배열 안의 객체들)를 미리 만들어 둡니다.
// 실무에서는 이 부분을 서버(DB)에서 받아온다고 생각하시면 됩니다.
const products = [
    { id:1, name:'삼성 세탁기', price:2500000, description:'다채로운 색상 조합은 어느 공간에서나 조화롭게 어우러짐'},
    { id:2, name:'엘지 냉장고', price:3500000, description:'AI 하이브리드 쿨링과 키친핏 디자인, 스마트싱스 연동 등 개인화 및 편의성을 극대화'},
    { id:3, name:'삼성 스타일러', price:150000, description:'옷을 위아래로 털어주며 좌우 흔들림(트위스트)을 더해 먼지를 효과적으로 제거' },
]

const ProductPage = () => {
    return (
        <div className='box'>
            <h1>상품목록</h1>
            <table>
                <tbody>
                    {/* 20. 자바스크립트의 map() 함수를 사용하여 배열(products)의 개수만큼 아래의 코드를 반복해서 그려냅니다. 
                        'p'는 배열 안의 각 객체(세탁기, 냉장고 등)를 하나씩 꺼내서 담는 임시 변수입니다. */}
                    {products.map(p =>
                        // 21. <React.Fragment>: 여러 개의 태그(<Product>와 <tr>)를 묶어주면서도, 
                        // 화면(HTML)에는 불필요한 <div> 같은 껍데기 태그를 남기지 않는 리액트의 특수 문법입니다.
                        // map으로 반복문을 돌릴 때는 리액트가 요소들을 구분할 수 있도록 반드시 고유한 'key' 값을 넣어주어야 합니다.
                        <React.Fragment key={p.id}>
                            
                            {/* 22. 불러온 자식 컴포넌트(<Product>)에 'product'라는 이름표(prop)를 달아서 
                                현재 꺼내온 데이터 객체 'p'를 통째로 전달해 줍니다. */}
                            <Product product={p}/>
                            
                            {/* 23. 인라인 스타일을 사용해 텍스트를 왼쪽으로 정렬합니다. */}
                            <tr style={{textAlign:'left'}}>
                                {/* 24. 꺼내온 객체 'p' 안의 description(상품 설명) 값을 출력합니다. */}
                                <td colSpan={3}>{p.description}</td>
                            </tr>
                        </React.Fragment>
                    )}
                </tbody>
            </table>
        </div>
    )
}
export default ProductPage
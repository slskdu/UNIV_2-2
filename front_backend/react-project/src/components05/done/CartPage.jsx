// 1~4. 장바구니와 주문 목록 간의 데이터 이동(등록, 삭제, 주문, 취소)을 구현합니다.
import React, { useReducer, useState, useRef } from 'react'
import Product from './Product' // 각 상품 리스트를 그려줄 자식 컴포넌트
import '../Style05.css'

// 8. 오타 방지를 위해 액션 타입들을 미리 상수로 정의합니다.
// 다른 파일(Product 컴포넌트 등)에서도 쓸 수 있도록 'export'를 붙여 내보냅니다.
export const ACTION_TYPE = {
    add:'등록',
    delete:'삭제',
    order:'주문',
    cancel:'취소'
}

// 14. [초핵심] 초기 상태(initState)가 매우 복잡합니다.
// 하나의 state 안에 cart(장바구니)와 order(주문)라는 두 개의 큰 덩어리가 있고, 각각 개수(count)와 배열(products)을 가집니다.
const initState = {
    cart: {
        count:1,
        products:[{ id:3, name:'삼성 냉장고'}]
    },
    order: {
        count:2,
        products:[{ id:1, name:'엘지 냉장고'}, {id:2, name:'엘지 세탁기'}]
    }
}

// 25. 상태를 변경하는 Reducer 함수입니다.
const reducer = (state, action) => {
    switch(action.type){
        case ACTION_TYPE.add:
            // [매우 중요] 중첩 객체의 상태를 업데이트하는 공식입니다.
            return {
                ...state, // 1. 우선 기존 state 전체(cart, order 둘 다)를 그대로 복사해 옵니다.
                cart:{    // 2. 그 중 'cart' 부분만 새롭게 덮어씌웁니다.
                    count: state.cart.count + 1, // 개수는 1 증가
                    // 배열의 기존 요소들(...state.cart.products) 뒤에 새로 전달받은 상품(action.product)을 추가합니다.
                    products: [...state.cart.products, action.product] 
                }
            }
        case ACTION_TYPE.delete:
            return {
                ...state,
                cart:{
                    count: state.cart.count - 1, // 개수는 1 감소
                    // filter를 이용해 삭제하려는 상품의 id와 '다른' 것들만 남겨 새 배열을 만듭니다.
                    products: state.cart.products.filter(p => p.id !== action.product.id)
                }
            }
        case ACTION_TYPE.order:
            // 장바구니에서 주문으로 넘어가는 기능입니다. (cart에서는 삭제, order에는 추가)
            return {
                ...state,
                cart:{
                    count: state.cart.count - 1,
                    products: state.cart.products.filter(p => p.id !== action.product.id)
                },
                order:{
                    count: state.order.count + 1,
                    products: [...state.order.products, action.product]
                }
            }
        case ACTION_TYPE.cancel:
            // 주문에서 장바구니로 다시 돌아가는 기능입니다. (위의 order와 반대로 동작)
            return {
                ...state,
                order:{
                    count: state.order.count - 1,
                    products: state.order.products.filter(p => p.id != action.product.id)
                },
                cart:{
                    count: state.cart.count + 1,
                    products: [...state.cart.products, action.product]
                }
            }
        default:
            return state;
    }
}

const CartPage = () => {
    const [name, setName] = useState('삼성 세탁기');
    const [state, dispatch] = useReducer(reducer, initState);
    const idRef = useRef(4) // 새 상품이 등록될 때 부여할 고유 ID입니다.
    
    return (
        <div className='box'>
            <div>
                <h1>카트 목록</h1>
                {/* 77. 중첩 객체이므로 state.cart.count 로 접근해야 합니다. */}
                <h5>상품수:{state.cart.count}개</h5>
                <input value={name} onChange={(e)=>setName(e.target.value)} placeholder='상품이름'/>
                
                {/* 79. 등록 버튼 클릭 시 dispatch 발송
                    주문서(액션)에 type과 함께 새롭게 만들 product 객체(id, name)를 포장해서 보냅니다. */}
                <button onClick={()=>dispatch({type:ACTION_TYPE.add, product:{id:idRef.current++, name}})}>등록</button>
                
                <table>
                    <tbody>
                        {/* 82. 카트 배열을 반복해서 자식 컴포넌트로 그립니다. */}
                        {state.cart.products.map(p=>
                            // [핵심] 자식 컴포넌트 안에서 삭제/주문 버튼을 눌러야 하므로 'dispatch' 함수 자체를 Props로 넘겨줍니다!
                            <Product key={p.id} product={p} type='cart' dispatch={dispatch}/>
                        )}
                    </tbody>
                </table>
            </div>
            <div>
                <h1>주문 목록</h1>
                <h5>상품수:{state.order.count}개</h5>
                <tbody> {/* (참고: HTML 표준상 <tbody> 겉에 <table> 태그가 있어야 더 완벽합니다.) */}
                    {state.order.products.map(p=>
                        // 주문 목록에 있는 상품들도 마찬가지로 dispatch를 넘겨주어 취소가 가능하게 합니다.
                        <Product key={p.id} product={p} type='order' dispatch={dispatch}/>
                    )}
                </tbody>
            </div>
        </div>
    )
}
export default CartPage
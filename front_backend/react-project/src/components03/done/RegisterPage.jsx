// 1~4. 상품 목록 출력, 폼 상태 관리, useRef를 이용한 ID 증가 및 포커스, 우클릭 삭제 기능을 구현합니다.
import React, { useRef, useState } from 'react'
import '../Style03.css'

// 7~11. 초기 상품 목록 데이터 배열입니다.
const data = [
    { id: 1, name: '삼성 세탁기', price: 2500000},
    { id: 2, name: '엘지 냉장고', price: 3500000},
    { id: 3, name: '삼성 스타일러', price: 150000},
]

// 13. 입력 양식(Form)의 초기값을 객체 형태로 미리 정의해 둡니다.
const initForm = {name:'엘지 TV', price:2000000};

const RegisterPage = () => {
    // 16. 전체 상품 목록 배열을 관리하는 상태 변수입니다.
    const [products, setProducts] = useState(data);
    
    // 17. [중요] 새로운 상품이 추가될 때 부여할 고유 ID 번호입니다. 
    // 값이 바뀌어도 화면이 다시 그려질(렌더링) 필요가 없는 데이터이므로 state 대신 useRef를 사용해 4부터 시작합니다.
    const idRef = useRef(4);
    
    // 18. 이름 입력상자에 마우스 포커스를 주기 위한 용도입니다.
    const nameRef = useRef(null);

    // 20. 입력 양식의 상태를 객체 하나로 묶어서 관리합니다.
    const [form, setForm] = useState(initForm);
    const {name, price} = form; // 구조 분해 할당으로 name과 price를 꺼내 씁니다.
    
    // 22. [초핵심] 모든 입력상자의 변화를 이 함수 하나로 처리합니다.
    const onChange = (e) => {
        // 기존 form 객체(...form)를 복사한 뒤, 
        // 이벤트가 발생한 태그의 name 속성 값([e.target.name])을 키(key)로 삼아,
        // 입력된 값(e.target.value)으로 덮어씌웁니다.
        setForm({...form, [e.target.name]: e.target.value});
    }

    // 26. 폼의 전송(submit) 버튼을 눌렀을 때 실행됩니다.
    const onSubmit = (e) => {
        // 27. 폼이 제출되면서 페이지가 새로고침되는 브라우저 기본 동작을 막습니다.
        e.preventDefault();
        
        // 28. 기존 배열(...products)을 복사하고, 맨 뒤에 새 객체를 추가합니다.
        // id는 idRef의 현재 값을 쓴 뒤 1 증가시킵니다(++). 나머지는 form 객체의 데이터를 복사(...form)합니다.
        setProducts([...products, {id: idRef.current++, ...form}]);
        
        // 29. 등록 후 이름 입력상자로 커서를 이동시킵니다.
        nameRef.current.focus();
    }

    // 32. 폼의 취소(reset) 버튼을 눌렀을 때 실행됩니다.
    const onReset = (e) => {
        e.preventDefault(); // 기본 새로고침 방지
        setForm(initForm);  // 폼 상태를 다시 초기값으로 되돌립니다.
        nameRef.current.focus();
    }

    // 38. 상품 행에서 마우스 우클릭을 했을 때(삭제) 실행됩니다.
    const onRemove = (e, id) => {
        e.preventDefault(); // 우클릭 시 기본 메뉴창이 뜨는 것을 막습니다.
        
        // 40. 확인(confirm) 창을 띄워 '확인(true)'을 눌렀을 때만 실행합니다.
        if(window.confirm(`${id}번 상품을 삭제하실래요?`)){
            // 41. [초핵심] 자바스크립트의 filter() 함수를 사용하여 데이터 삭제를 구현합니다.
            // 전체 상품 중 누른 행의 id와 '다른' 상품들만 걸러내서 새로운 배열(data)을 만듭니다.
            const data = products.filter(p => p.id !== id);
            
            // 42. 걸러진 새 배열로 상태를 업데이트합니다. (결과적으로 해당 id 상품만 화면에서 사라짐)
            setProducts(data);
        }
    }

    return (
        <div className='box'>
            <h1>상품등록</h1>
            {/* 49. form 태그에 onSubmit과 onReset 이벤트를 연결합니다. */}
            <form onSubmit={onSubmit} onReset={onReset}>
                
                {/* 50~51. name 속성이 매우 중요합니다. onChange 함수에서 [e.target.name]으로 사용됩니다. */}
                <input value={name} onChange={onChange} ref={nameRef}
                    placeholder='상품이름' name='name'/>
                <input value={price} onChange={onChange} 
                    placeholder='상품가격' name='price' type='number' step={1000}/>
                
                <div>
                    {/* 55. 이름이나 가격이 비어있으면 등록 버튼을 비활성화(disabled) 합니다. */}
                    <button disabled={!name || !price} type='submit'>등록</button>
                    {/* 56. 현재 폼 상태가 초기 폼 상태와 완벽히 똑같으면 취소 버튼을 비활성화 합니다. */}
                    <button disabled={JSON.stringify(form)===JSON.stringify(initForm)} type='reset'>취소</button>
                </div>
            </form>
            <table>
                <tbody>
                    {products.map(p=>
                        // 63. 반복 생성되는 최상위 태그에 key를 넣고, 우클릭 시 onRemove 함수에 클릭한 행의 id를 넘겨줍니다.
                        <tr key={p.id} onContextMenu={(e)=>onRemove(e, p.id)}>
                            <td>{p.id}</td>
                            <td>{p.name}</td>
                            {/* 66. 숫자에 천 단위 콤마(,)를 찍어주는 자바스크립트 내장 함수입니다. */}
                            <td>{parseInt(p.price).toLocaleString()}원</td>
                        </tr>
                    )}
                </tbody>
            </table>
        </div>
    )
}
export default RegisterPage
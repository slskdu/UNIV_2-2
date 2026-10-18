// 1. useEffect를 이용해 화면이 렌더링될 때 혹은 특정 값이 바뀔 때마다 원하는 작업을 실행하는 실습입니다.
import React, { useEffect, useState } from 'react' // 2. useState와 함께 useEffect 훅을 불러옵니다.
import '../Style04.css'

const InforPage = () => {
    // 6~7. 이름과 나이를 관리할 상태 변수를 만듭니다. 초기값은 각각 '홍길동', 20 입니다.
    const [name, setName] = useState('홍길동');
    const [age, setAge] = useState(20);

    // 9. [이번 코드의 초핵심 포인트] 부수 효과(Effect)를 처리하는 useEffect 입니다.
    useEffect(()=>{
        // 10. 콘솔창에 현재 이름과 나이를 출력합니다.
        console.log(name, age);
        
    // 11. [매우 중요] 대괄호 [ ] 안에 'age'만 들어있습니다. 이를 '의존성 배열'이라고 부릅니다.
    // 이 코드의 의미는 "처음 화면이 켜질 때 한 번 실행하고, 그 다음부터는 'age' 값이 변할 때만 이 안의 코드를 실행해라!" 입니다.
    // 즉, 사용자가 이름 입력상자에 글자를 쳐서 'name' 값이 아무리 바뀌어도 이 부분은 절대 실행되지 않습니다.
    }, [age]); 

    return (
        <div className='box'>
            {/* 15. 현재 상태(name, age)를 화면에 텍스트로 보여줍니다. */}
            <h1>이름:{name}, 나이:{age}</h1>
            
            {/* 16~17. 이름 입력상자 
                - value={name}: 화면의 값을 리액트 상태와 일치시킵니다.
                - onChange: 값이 변할 때 e.target.value를 가져와 setName으로 업데이트합니다. */}
            <input placeholder='이름' 
                value={name} onChange={(e)=>setName(e.target.value)}/>
                
            {/* 18~19. 나이 입력상자 
                - step={2}: 위아래 화살표를 누를 때마다 2씩 증감하도록 설정합니다. */}
            <input placeholder='나이' type='number' step={2}
                value={age} onChange={(e)=>setAge(e.target.value)}/>
        </div>
    )
}

export default InforPage
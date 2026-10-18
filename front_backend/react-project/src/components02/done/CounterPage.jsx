// 1~3. 일반 변수와 state(상태) 변수의 차이를 확인하는 예제입니다.
// 4. 리액트에서 상태를 관리하기 위해 'useState'라는 Hook(기능)을 불러옵니다. 중괄호 { } 안에 써야 합니다.
import React, { useState } from 'react' 
import '../Style02.css'

// 7. CounterPage라는 컴포넌트를 선언합니다.
const CounterPage = () => {
    // 8. 일반 변수로 선언하면 값이 증가해도 화면(렌더링)에 반영되지 않습니다.
    // let number = 0; 

    // 9. 리액트의 핵심 상태 관리 문법입니다.
    // number: 현재 상태 값을 담는 변수
    // setNumber: number의 값을 변경해주는 전용 함수
    // useState(0): number의 초기값을 0으로 설정합니다.
    const [number, setNumber] = useState(0);

    // 11. 증가 버튼을 눌렀을 때 실행될 함수입니다.
    const onIncrease = () => {
        // 12. 상태 변수는 number++ 처럼 직접 값을 변경하면 절대 안 됩니다! 리액트가 값의 변화를 감지하지 못합니다.
        // number++; 
        
        console.log(number); // 콘솔창에 현재 숫자를 출력해 봅니다.
        
        // 14. 반드시 상태를 변경하는 전용 함수(setNumber)를 사용해야 화면이 리렌더링(업데이트) 됩니다.
        setNumber(number + 1); 
    }

    // 16. 감소 버튼을 눌렀을 때 실행될 함수입니다.
    const onDecrease = () => {
        console.log(number);
        // 19. 전용 함수를 통해 현재 number 값에서 1을 뺀 값으로 상태를 업데이트합니다.
        setNumber(number - 1);
    }

    return (
        <div className='box'>
            {/* 24. 상태 변수 number를 화면에 출력합니다. setNumber가 실행될 때마다 이 부분이 새로운 숫자로 바뀝니다. */}
            <h1>{number}</h1>
            
            {/* 25~26. 버튼을 클릭했을 때(onClick) 함수를 실행합니다. 
                주의: 리액트의 이벤트는 카멜표기법(onClick)으로 적고, 함수 이름만 중괄호 안에 넣어야 합니다. */}
            <button onClick={onDecrease}>감소</button>
            <button onClick={onIncrease}>증가</button>
        </div>
    )
}

// 30. 컴포넌트를 외부로 내보냅니다.
export default CounterPage
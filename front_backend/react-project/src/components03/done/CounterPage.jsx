// 1~4. 마우스 좌클릭(증가), 우클릭(감소), 엔터키(경고창) 이벤트를 다루는 실습입니다.
import React, { useState } from 'react'
import '../Style03.css'

const CounterPage = () => {
    // 8. 숫자를 관리할 상태 변수 number를 생성하고 초기값을 0으로 설정합니다.
    const [number, setNumber] = useState(0);

    // 10. 마우스 왼쪽 버튼을 클릭했을 때(onClick) 실행될 함수입니다.
    const onClickLeft = () => {
        setNumber(number+1); // 값을 1 증가시킵니다.
    }

    // 14. 마우스 오른쪽 버튼을 클릭했을 때(onContextMenu) 실행될 함수입니다.
    // 매개변수 'e'는 이벤트(Event) 객체로, 발생한 이벤트에 대한 모든 정보를 담고 있습니다.
    const onClickRight = (e) => {
        // 15. [매우 중요] 브라우저의 기본 동작을 막아주는 함수입니다.
        // 웹페이지에서 마우스 우클릭을 하면 원래 옵션 메뉴창이 뜨는데, 그것이 안 뜨게 막아줍니다.
        e.preventDefault(); 
        setNumber(number-1); // 값을 1 감소시킵니다.
    }

    // 19. 키보드의 키를 눌렀을 때(onKeyDown) 실행될 함수입니다.
    const onKeyDown = (e) => {
        // 20. 누른 키(e.key)가 'Enter'인지 확인합니다. (대소문자 구분에 주의!)
        if(e.key === 'Enter') {
            // 백틱(`)을 사용하여 변수와 문자열을 함께 출력합니다.
            alert(`현재 값은 ${number}입니다.`); 
        }
    }
    
    return (
        <div className='box'>
            {/* 27~28. 입력 상자(input) 태그입니다.
                - onKeyDown: 키보드를 누를 때 작동합니다.
                - value={number}: 입력 상자의 화면 값을 리액트의 number 상태와 완벽히 동기화시킵니다.
                - onChange: 상자 안의 값이 바뀔 때마다 작동합니다. 
                  [중요] e.target.value는 사용자가 입력한 값을 문자열로 가져옵니다. 
                  이를 숫자로 사용하기 위해 parseInt()로 감싸서 상태를 변경(setNumber)합니다. */}
            <input style={{width:'100px'}} type='number' onKeyDown={onKeyDown}
                value={number} onChange={(e)=>setNumber(parseInt(e.target.value))}/>
            
            {/* 29. 버튼 태그입니다.
                - onClick: 일반적인 좌클릭 이벤트
                - onContextMenu: 마우스 우클릭 이벤트 */}
            <button onClick={onClickLeft} onContextMenu={onClickRight}>증감</button>
        </div>
    )
}
export default CounterPage
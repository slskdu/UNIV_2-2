// 1~4. 은행 계좌의 입금, 출금, 이자, 해지 기능을 useReducer로 구현하는 실습입니다.
import React, { useState, useReducer, useRef } from 'react'
import '../Style05.css'

// 7. [초핵심] 액션(Action) 타입 정의
// 오타를 방지하기 위해 명령어(문자열)들을 미리 객체로 만들어 둡니다.
const ACTION_TYPE = {
    deposit:'입금',
    withdrawal:'출금',
    interest:'이자',
    close:'해지'
}

// 14~15. 초기 잔액과 이율 상수 설정
const initState = 10000; 
const rate = 0.1; 

// 17. [초핵심] 리듀서(Reducer) 함수 정의
// 컴포넌트 밖에 만들어지며, "현재 상태(state)"와 "어떤 작업을 할지 담긴 주문서(action)"를 받아서 "새로운 상태"를 반환합니다.
const reducer = (state, action) => {
    // 18. action 객체에 담겨 온 money 값을 정수로 변환합니다. (이자, 해지 버튼을 누를 때는 money가 전달되지 않으므로 NaN이 되지만, 아래 로직에서 쓰지 않으므로 에러가 나지 않습니다.)
    const money = parseInt(action.money);
    
    // 19. switch문을 사용해 주문서의 '타입(action.type)'에 따라 알맞은 계산을 수행합니다.
    switch(action.type){
        case ACTION_TYPE.deposit:
            // 입금: 현재 잔액(state)에 금액(money)을 더해서 반환
            return state + money; 
        case ACTION_TYPE.withdrawal:
            // 출금: 잔액이 부족하면 경고창을 띄우고 기존 잔액(state)을 그대로 유지(return state)
            if(state < money){
                alert('잔액이 부족합니다!');
                return state;
            }
            // 잔액이 충분하면 뺀 값을 반환
            return state - money;
        case ACTION_TYPE.interest:
            // 이자: 잔액 + (잔액 * 이율) 반환
            return state + (state * rate);
        case ACTION_TYPE.close:
            // 해지: 잔액을 0으로 만듦
            return 0;
        default:
            // 알 수 없는 타입이 들어오면 기존 상태를 그대로 유지 (필수 방어코드)
            return state;
    }
}

const BankPage = () => {
    // 38. 입력상자에 적는 '금액'을 관리하는 일반 상태입니다.
    const [money, setMoney] = useState(1000);
    
    // 39. [초핵심] useReducer 훅 사용
    // state: 현재 잔액 / dispatch: 리듀서에게 주문서(action)를 보내는 함수
    // useReducer(리듀서함수, 초기값) 형태로 선언합니다.
    const [state, dispatch] = useReducer(reducer, initState);
    
    // 40. (이 코드에서는 쓰이지 않았지만 거래 내역 리스트 등을 만들 때 사용할 수 있는 ID 변수입니다.)
    const idRef = useRef(2);

    return (
        <div className='box'>
            {/* 44. 현재 잔액(state)을 정수로 바꾸고 쉼표를 찍어 출력합니다. */}
            <h1>잔액:{parseInt(state).toLocaleString()}원</h1>
            <div>
                <span>금액:</span>
                <input value={money} onChange={(e)=>setMoney(e.target.value)} placeholder='금액' type='number' step={1000}/>
            </div>
            <div>
                {/* 49. [초핵심] dispatch 함수를 사용해 { type, money } 가 담긴 주문서(객체)를 reducer로 발송합니다.
                    예: dispatch({ type: '입금', money: 1000 }) */}
                <button onClick={()=>dispatch({type:ACTION_TYPE.deposit, money})}>입급</button>
                <button onClick={()=>dispatch({type:ACTION_TYPE.withdrawal, money})}>출금</button>
                
                {/* 51~52. 이자와 해지는 추가 금액(money) 데이터가 필요 없으므로 type만 보냅니다. */}
                <button onClick={()=>dispatch({type:ACTION_TYPE.interest})}>이자</button>
                <button onClick={()=>dispatch({type:ACTION_TYPE.close})}>해지</button>
            </div>
        </div>
    )
}
export default BankPage
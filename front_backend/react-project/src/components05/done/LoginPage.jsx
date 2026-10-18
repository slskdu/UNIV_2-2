// 처리조건: 이메일 '@' 포함 여부 검사, 비밀번호 6자리 이상 검사, 폼 전체 유효성 검사 후 submit
import React, { useReducer, useState } from 'react'
import '../Style05.css'

// 6. 에러 발생 시 화면에 띄워줄 메시지들을 미리 객체로 정의해 둡니다.
const ERROR_MESSAGE = {
    email:'유효한 이메일 주소를 입력하세요!',
    password:'비밀번호는 6자이상 입력하세요!'
}

// 11. 리듀서에서 사용할 액션(명령어) 타입들입니다.
const ACTION_TYPE = {
    email:'이메일',
    password:'비밀번호',
}

// 16. [초핵심] 초기 상태 설정
// email과 password 각각 입력된 값(value)과 유효성 통과 여부(valid)를 상태로 가집니다.
// formValid는 이메일과 비밀번호가 모두 유효한지(true) 최종 확인하는 상태입니다.
const initState = {
    email: { value:'', valid:false },
    password:{ value:'', valid:'' }, // (주의: valid 초기값은 false가 더 적절하나 빈 문자열로 두었습니다.)
    formValid:false,
}

// 30. 상태를 변경하는 Reducer 함수입니다.
const reducer = (state, action) => {
    switch(action.type){
        case ACTION_TYPE.email:
            // 33. [핵심] 자바스크립트 내장 함수인 includes()를 사용해 문자열에 '@'가 포함되어 있는지(true/false) 확인합니다.
            const emailValid = action.value.includes('@');
            return {
                ...state, // 기존 상태 복사
                email: {
                    value: action.value,  // 방금 입력한 값으로 업데이트
                    valid: emailValid,    // 방금 검사한 유효성 결과(true/false)로 업데이트
                },
                // 39. [초핵심] 폼 전체 유효성 검사
                // 방금 검사한 이메일 유효성(emailValid) '그리고(&&)' 기존 비밀번호의 유효성(state.password.valid)이 모두 true일 때만 true가 됩니다.
                formValid: emailValid && state.password.valid 
            }
            
        case ACTION_TYPE.password:
            // 42. 입력된 비밀번호의 길이(length)가 6 이상인지(true/false) 확인합니다.
            const passwordValid = action.value.length >= 6;
            return {
                ...state,
                password: {
                    value: action.value,
                    valid: passwordValid,
                },
                // 49. 이번에는 방금 검사한 비밀번호 유효성 '그리고(&&)' 기존 이메일 유효성을 비교합니다.
                formValid: passwordValid && state.email.valid
            }
        default:
            return state;
    }
}

const LoginPage = () => {
    const [state, dispatch] = useReducer(reducer, initState);
    
    // 59. 폼이 제출(submit)될 때 실행될 함수입니다.
    const onSubmit = (e) => {
        e.preventDefault(); // 새로고침 방지

        // 62. 리듀서에서 계산해 둔 최종 유효성(formValid) 값에 따라 다르게 동작합니다.
        if(state.formValid) {
            alert('유효성체크 완료!');
        }else{
            alert('유효성체크 미완료!');
        }
    }

    return (
        <form className='box' onSubmit={onSubmit}>
            {/* 70~72. 이메일 입력상자
                값이 입력될 때마다 dispatch를 통해 {type: '이메일', value: 현재입력값} 형태의 액션을 보냅니다. */}
            <input value={state.email.value} 
                onChange={(e)=>dispatch({type:ACTION_TYPE.email, value:e.target.value})} 
                placeholder='이메일'/>
                
            {/* 73. [초핵심] 에러 메시지 조건부 렌더링 (단축 평가)
                OR 연산자(||)는 앞의 값이 거짓(false)이면 뒤의 값을 출력합니다.
                즉, valid가 false(유효성 검사 실패)일 때만 ERROR_MESSAGE를 화면에 보여줍니다. */}
            <span className='error'>{state.email.valid || ERROR_MESSAGE.email}</span><br/>
            
            <input onChange={(e)=>dispatch({type:ACTION_TYPE.password, value:e.target.value})} 
                value={state.password.value} 
                placeholder='비밀번호' type='password'/>
            <span className='error'>{state.password.valid || ERROR_MESSAGE.password}</span><br/>
            
            <button type='submit'>로그인</button>
        </form>
    )
}

export default LoginPage
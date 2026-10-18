// 1~3. 컴포넌트 계층 구조(RootPage -> Page -> Header, Content, Footer)와 다크모드 변경 목표를 설명합니다.
import React, { useState } from 'react'
import '../../Style06.css' // 외부 스타일시트를 불러옵니다.
import Page from './Page'  // 6. 자식 컴포넌트인 Page를 불러옵니다.

const RootPage = () => {
    // 9. [초핵심] 다크 모드 켜짐/꺼짐 상태를 관리할 변수(dark)입니다.
    // 스위치처럼 켜고 끄는(On/Off) 상태이므로 초기값을 'false(거짓)'로 설정합니다.
    const [dark, setDark ]= useState(false);
    
    return (
        <div>
            {/* 12. 불러온 자식 컴포넌트(<Page />)에 상태 변수(dark)와 상태 변경 함수(setDark)를 
                모두 이름표(Props)를 달아서 전달합니다. */}
            <Page dark={dark} setDark={setDark}/>
        </div>
    )
}
export default RootPage
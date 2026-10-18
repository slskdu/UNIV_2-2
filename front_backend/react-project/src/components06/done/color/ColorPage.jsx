// 1. 색상 선택을 클릭(좌클릭/우클릭)하면 박스의 색상이 변하는 실습입니다.
import React from 'react'
import ColorSelect from './ColorSelect' // 3. 색상을 선택할 버튼들이 있는 컴포넌트
import ColorBox from './ColorBox'       // 4. 색상이 실제로 변해서 보여질 박스 컴포넌트
import '../../Style06.css'
import ColorProvider from './ColorProvider' // 6. [초핵심] 색상 상태(State)들을 모아둔 물류 창고(Provider) 컴포넌트입니다.

const ColorPage = () => {
    return (
        // 9. [초핵심] ColorProvider로 하위 태그들을 모두 감싸줍니다!
        // 이렇게 감싸주면, 안에 있는 ColorSelect와 ColorBox는 부모가 굳이 데이터를 넘겨주지 않아도
        // 스스로 ColorProvider 창고에 접근해서 필요한 데이터(색상 값이나 변경 함수)를 꺼내 쓸 수 있습니다.
        <ColorProvider>
            <div className='box'>
                {/* 12~14. Props가 하나도 없는 것을 주목하세요! 
                    이전 방식(Props Drilling)이었다면 <ColorSelect color={color} setColor={setColor} ...> 
                    처럼 주렁주렁 달아주었어야 하지만, Context API 덕분에 코드가 아주 깔끔해졌습니다. */}
                <ColorSelect/>
                <hr/> {/* 13. 화면에 수평선(가로줄)을 그어주는 HTML 태그입니다. */}
                <ColorBox/>
            </div>
        </ColorProvider>
    )
}

export default ColorPage
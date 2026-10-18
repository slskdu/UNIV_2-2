// 1~4. useEffect를 이용한 외부 API 호출, 페이지네이션(페이징), 그리고 토글(열기/닫기) 기능을 구현한 핵심 예제입니다.
import React, { useEffect, useState, useRef } from 'react'
import PageButton from './PageButton' // 하단에 페이지 이동 버튼들을 그려줄 자식 컴포넌트입니다.
import '../Style04.css'

const PostPage = () => {
    // 8. 화면에 보여줄 게시글 목록 데이터를 담을 상태(배열)입니다.
    const [posts, setPosts] = useState([]);
    
    // 9. 현재 페이지 번호를 관리하는 상태입니다. 초기값은 1페이지입니다.
    const [page, setPage] = useState(1);
    
    // 10. 한 페이지당 보여줄 게시글의 개수(5개)를 고정값으로 설정합니다.
    const size = 5;
    
    // 11. 마지막 페이지 번호를 저장할 참조 변수입니다. 
    // 마지막 페이지 값은 렌더링에 직접 관여하기보단 자식에게 전달할 용도이므로 useRef를 썼습니다.
    const lastRef = useRef(1);

    // 13. 외부 서버(JSONPlaceholder)에서 데이터를 가져오는 함수입니다.
    const callAPI = () => {
        // 14. fetch() 함수로 해당 URL에 요청을 보냅니다.
        fetch('https://jsonplaceholder.typicode.com/posts')
        .then(response => response.json()) // 15. 응답(response)이 오면 JSON 형태로 변환합니다.
        .then(json => { // 16. 변환된 JSON 데이터를 받아와서 처리합니다.
            // 17~18. 현재 페이지에 맞는 시작 번호와 끝 번호를 계산합니다.
            // 예: 1페이지면 start=1, end=5 / 2페이지면 start=6, end=10
            const start = (page-1) * size + 1;
            const end = page * size;
            
            // 19. filter 함수를 이용해 100개의 전체 데이터 중 start~end 번호에 해당하는 5개만 걸러냅니다.
            const data = json.filter(post => post.id >= start && post.id <= end);
            console.log(data); // 확인용 콘솔 출력
            
            // 21. 걸러낸 5개의 데이터로 posts 상태를 업데이트하여 화면을 그립니다.
            setPosts(data);
            
            // 22. 전체 데이터 개수를 5(size)로 나눈 뒤 올림(Math.ceil)하여 마지막 페이지 번호를 구합니다. (100/5 = 20페이지)
            lastRef.current = Math.ceil(json.length / size);
        });
    }

    // 25. [초핵심] 컴포넌트가 화면에 나타날 때, 혹은 특정 상태(page)가 변할 때마다 실행되는 Hook입니다.
    useEffect(()=>{
        callAPI(); // API를 호출하여 데이터를 가져옵니다.
    // 27. 의존성 배열(Dependency Array)에 [page]가 들어있습니다.
    // 즉, "page 상태가 변경될 때마다 이 useEffect 안의 코드(callAPI)를 다시 실행해라!" 라는 뜻입니다.
    }, [page]);

    // 29. 게시글 제목을 클릭했을 때 본문을 열고 닫는(Toggle) 함수입니다.
    const onClick = (id) => {
        // 30. [초핵심] map을 사용하여 배열 안의 특정 객체 데이터만 수정하는 리액트 표준 문법입니다.
        // 클릭한 게시물의 id와 일치하는 객체를 찾으면, 해당 객체를 복사(...post)한 뒤 
        // isVisible 속성을 기존 값의 반대(!post.isVisible)로 덮어씌웁니다. 일치하지 않으면 그대로 둡니다.
        const data = posts.map(post => post.id === id ? {...post, isVisible: !post.isVisible} : post);
        setPosts(data);
    }

    return (
        <div className='box'>
            <h1>Posts</h1>
            {/* 36. posts 배열을 순회하며 화면에 출력합니다. */}
            {posts.map(post=>
                <div key={post.id}>
                    {/* 38. onClick 함수에 id 값을 전달하기 위해 반드시 괄호 없는 화살표 함수 '() =>' 로 감싸야 합니다. */}
                    <h5 onClick={()=>onClick(post.id)} className='title'>{post.id}. {post.title}</h5>
                    
                    {/* 39. 논리 연산자(&&)를 이용한 조건부 렌더링입니다. 
                        post.isVisible 값이 true일 때만 뒤의 <div className='body'>가 렌더링(출력)됩니다. */}
                    {post.isVisible && <div className='body'>{post.body}</div>}
                </div>
            )}
            
            {/* 42. 페이징 버튼 컴포넌트에 현재 페이지(page), 마지막 페이지(last), 페이지 변경 함수(setPage)를 Props로 넘겨줍니다. */}
            <PageButton page={page} last={lastRef.current} setPage={setPage}/>
        </div>
    )
}
export default PostPage